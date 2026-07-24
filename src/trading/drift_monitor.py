"""Concept-drift monitor + auto-quarantine (Phase 2C.3).

Compares each live / shadow strategy's realized-return stream against its
walk-forward backtest expectation and flags decay two ways:

* **CUSUM** — a one-sided cumulative-sum detector that alarms on sustained
  underperformance below the backtest mean (needs only the scalar reference we
  already store in ``backtest_weights.json``).
* **KS two-sample** — when a reference *sample* is available, a Kolmogorov–
  Smirnov test flags a significant distribution shift that is also *worse* than
  the reference (so improvement never triggers a quarantine).

Both are implemented in pure Python — no scipy / new dependency. A confirmed
drift writes ``trading:quarantine:{strategy}`` (read by the Orchestrator to
exclude the strategy's buys) and is surfaced by the Telegram cockpit.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from src.database.cache import cache
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

QUARANTINE_KEY = "trading:quarantine:{name}"
_QUARANTINE_TTL = 30 * 86400


# ── Pure statistics ──────────────────────────────────────────────────────────


def cusum_decay(
    values: list[float], target: float, threshold: float, slack: float = 0.0
) -> bool:
    """One-sided downward CUSUM: True if sustained underperformance vs target."""
    s = 0.0
    for x in values:
        s = max(0.0, s + (target - x) - slack)
        if s > threshold:
            return True
    return False


def ks_statistic(a: list[float], b: list[float]) -> float:
    """Two-sample Kolmogorov–Smirnov D statistic (max ECDF gap)."""
    if not a or not b:
        return 0.0
    sa = sorted(a)
    sb = sorted(b)
    n1, n2 = len(sa), len(sb)
    d = 0.0
    for v in sorted(sa + sb):
        fa = bisect.bisect_right(sa, v) / n1
        fb = bisect.bisect_right(sb, v) / n2
        d = max(d, abs(fa - fb))
    return d


def ks_pvalue(d: float, n1: int, n2: int) -> float:
    """Asymptotic p-value for the two-sample KS statistic ``d``."""
    if n1 == 0 or n2 == 0 or d <= 0:
        return 1.0
    en = (n1 * n2 / (n1 + n2)) ** 0.5
    t = (en + 0.12 + 0.11 / en) * d
    # Q_ks(t) = 2 Σ (-1)^{k-1} e^{-2 k² t²}
    total = 0.0
    for k in range(1, 101):
        total += (-1) ** (k - 1) * math.exp(-2.0 * k * k * t * t)
    return max(0.0, min(1.0, 2.0 * total))


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def ks_drift(sample: list[float], reference: list[float], alpha: float) -> bool:
    """True if ``sample`` differs significantly from ``reference`` AND is worse."""
    if len(sample) < 2 or len(reference) < 2:
        return False
    d = ks_statistic(sample, reference)
    p = ks_pvalue(d, len(sample), len(reference))
    return p < alpha and _mean(sample) < _mean(reference)


# ── Monitor ──────────────────────────────────────────────────────────────────


@dataclass
class DriftVerdict:
    strategy: str
    drift: bool
    reason: str
    sample_mean: float
    reference_mean: float
    samples: int


class DriftMonitor:
    """Detects strategy decay and quarantines the worst offenders.

    ``fetch_returns`` yields a strategy's recent realized per-signal returns;
    ``reference_mean`` yields its backtest expected per-signal return;
    ``reference_sample`` (optional) yields a backtest return sample for the KS
    test. All injected so the monitor is pure-testable.
    """

    def __init__(
        self,
        fetch_returns: Callable[[str], Awaitable[list[float]]],
        reference_mean: Callable[[str], Awaitable[float]],
        reference_sample: Callable[[str], Awaitable[list[float]]] | None = None,
    ) -> None:
        self._fetch_returns = fetch_returns
        self._reference_mean = reference_mean
        self._reference_sample = reference_sample

    async def check(self, strategy: str) -> DriftVerdict:
        returns = await self._fetch_returns(strategy) or []
        ref_mean = await self._reference_mean(strategy)
        if len(returns) < settings.drift_min_samples:
            return DriftVerdict(
                strategy,
                False,
                "insufficient samples",
                _mean(returns),
                ref_mean,
                len(returns),
            )
        reasons: list[str] = []
        if cusum_decay(
            returns,
            ref_mean,
            settings.drift_cusum_threshold,
            settings.drift_cusum_slack,
        ):
            reasons.append("cusum")
        if self._reference_sample is not None:
            ref = await self._reference_sample(strategy) or []
            if ks_drift(returns, ref, settings.drift_ks_alpha):
                reasons.append("ks")
        drift = bool(reasons)
        return DriftVerdict(
            strategy=strategy,
            drift=drift,
            reason=("+".join(reasons) if drift else "stable"),
            sample_mean=_mean(returns),
            reference_mean=ref_mean,
            samples=len(returns),
        )

    async def quarantine(self, strategy: str, reason: str) -> None:
        try:
            await cache.set(
                QUARANTINE_KEY.format(name=strategy),
                {"reason": reason},
                ttl=_QUARANTINE_TTL,
            )
            logger.warning("strategy_quarantined", strategy=strategy, reason=reason)
        except Exception as exc:  # noqa: BLE001
            logger.debug("quarantine_write_failed", strategy=strategy, error=str(exc))

    async def check_and_quarantine(self, strategies: list[str]) -> list[DriftVerdict]:
        """Check each strategy; quarantine those that drifted. Returns verdicts."""
        verdicts: list[DriftVerdict] = []
        for name in strategies:
            try:
                verdict = await self.check(name)
            except (
                Exception
            ) as exc:  # noqa: BLE001 — monitoring must never break the cycle
                logger.debug(
                    "drift_check_failed", strategy=name, error=type(exc).__name__
                )
                continue
            if verdict.drift:
                await self.quarantine(name, verdict.reason)
            verdicts.append(verdict)
        return verdicts


async def quarantined_strategies(names: list[str]) -> set[str]:
    """Names currently flagged quarantined (read by the Orchestrator)."""
    out: set[str] = set()
    for name in names:
        try:
            if await cache.get(QUARANTINE_KEY.format(name=name)):
                out.add(name)
        except Exception:  # noqa: BLE001, S112
            continue
    return out
