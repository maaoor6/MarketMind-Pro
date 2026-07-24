"""Cross-provider data-quality validation (live path + OHLCV).

Two layers:

1. **Structural** — reuses the backtest's :func:`validate_history` to trim
   corrupt leading segments (frozen runs / adjustment artifacts) from an OHLCV
   frame before any indicator sees it.
2. **Consensus** — when two or more providers return a price for the same
   instrument, disagreement beyond ``data_consensus_tolerance_pct`` is flagged
   and the robust (median) value is chosen. Staleness is flagged against
   ``data_quality_max_age_seconds``.

Everything is **fail-open**: a single available source is always accepted, and
missing data never blocks the pipeline harder than the existing fail-closed
context guards already do. Verdicts are advisory — Phase 2's DataValidation
agent consumes the persisted flags.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from src.backtest.validate import DataQualityReport, validate_history
from src.utils.config import settings
from src.utils.logger import get_logger
from src.utils.timezone_utils import now_utc

logger = get_logger(__name__)


@dataclass(frozen=True)
class DataQualityVerdict:
    """Outcome of a cross-source data-quality check.

    Attributes:
        ok: True if no blocking anomaly was found (fail-open).
        flags: Human-readable anomaly tags (e.g. ``"disagree:stooq"``).
        chosen_value: The value to use (median across sources), or None.
        sources: Mapping of provider name → its reported value.
    """

    ok: bool
    flags: list[str] = field(default_factory=list)
    chosen_value: float | None = None
    sources: dict[str, float] = field(default_factory=dict)


def consensus_price(
    values: dict[str, float],
    tolerance_pct: float | None = None,
) -> DataQualityVerdict:
    """Reconcile a price reported by one or more providers.

    Args:
        values: provider name → reported price.
        tolerance_pct: max allowed fractional deviation from the median before a
            source is flagged (defaults to ``settings.data_consensus_tolerance_pct``).

    Returns:
        A verdict whose ``chosen_value`` is the median of the inputs. With fewer
        than two values it is always ``ok`` (fail-open).
    """
    tol = (
        tolerance_pct
        if tolerance_pct is not None
        else settings.data_consensus_tolerance_pct
    )
    clean = {k: float(v) for k, v in values.items() if v is not None}
    if not clean:
        return DataQualityVerdict(ok=True, flags=["no_sources"], chosen_value=None)
    if len(clean) == 1:
        (only,) = clean.values()
        return DataQualityVerdict(ok=True, chosen_value=only, sources=clean)

    median = statistics.median(clean.values())
    flags: list[str] = []
    if median != 0:
        for name, val in clean.items():
            if abs(val - median) / abs(median) > tol:
                flags.append(f"disagree:{name}")
    return DataQualityVerdict(
        ok=not flags,
        flags=flags,
        chosen_value=median,
        sources=clean,
    )


def check_staleness(
    fetched_at: datetime,
    max_age_seconds: float | None = None,
    now: datetime | None = None,
) -> bool:
    """Return True if ``fetched_at`` is older than the allowed age."""
    limit = (
        max_age_seconds
        if max_age_seconds is not None
        else settings.data_quality_max_age_seconds
    )
    ref = now or now_utc()
    # Guard against tz-naive timestamps from some providers.
    if fetched_at.tzinfo is None:
        ref = ref.replace(tzinfo=None)
    return (ref - fetched_at).total_seconds() > limit


def validate_ohlcv(
    df: pd.DataFrame, ticker: str
) -> tuple[pd.DataFrame, DataQualityReport]:
    """Structural OHLCV cleaning — thin reuse of the backtest gate."""
    return validate_history(df, ticker)


# Redis key namespace read by the Phase-2 DataValidation agent + Telegram.
QUALITY_KEY = "data:quality:{ticker}"
_QUALITY_TTL = 3600


def crosscheck_providers() -> list[str]:
    """Configured secondary providers used to cross-check the reference close."""
    raw = getattr(settings, "data_crosscheck_providers", "") or ""
    return [n.strip().lower() for n in raw.split(",") if n.strip()]


async def _latest_close(provider, ticker: str) -> float | None:
    """Best-effort latest daily close from one provider (None on any failure)."""
    try:
        df = await provider.fetch_ohlcv(ticker, period="5d", interval="1d")
        if df is None or df.empty or "Close" not in df.columns:
            return None
        return float(df["Close"].iloc[-1])
    except Exception as exc:  # noqa: BLE001 — cross-check must never break the hot path
        logger.debug(
            "crosscheck_close_failed",
            provider=getattr(provider, "name", "?"),
            ticker=ticker,
            error=type(exc).__name__,
        )
        return None


async def cross_check_close(
    ticker: str,
    reference_close: float,
    reference_source: str = "yfinance",
    provider_names: list[str] | None = None,
    tolerance_pct: float | None = None,
) -> DataQualityVerdict:
    """Compare a reference close against configured secondary providers.

    Fail-open: if no secondary provider is configured or reachable the verdict
    is ``ok`` with a single source. Never raises.
    """
    names = provider_names if provider_names is not None else crosscheck_providers()
    values: dict[str, float] = {reference_source: float(reference_close)}
    if names:
        # Imported lazily to avoid a registry import cycle at module load.
        from src.data.registry import _FACTORIES, _register_defaults

        _register_defaults()
        for name in names:
            if name == reference_source:
                continue
            factory = _FACTORIES.get(name)
            if factory is None:
                continue
            try:
                close = await _latest_close(factory(), ticker)
            except Exception:  # noqa: BLE001
                close = None
            if close is not None:
                values[name] = close
    return consensus_price(values, tolerance_pct)


async def persist_verdict(ticker: str, verdict: DataQualityVerdict) -> None:
    """Store a verdict at ``data:quality:{ticker}`` for the validation agent."""
    from src.database.cache import cache

    payload = {
        "ok": verdict.ok,
        "flags": verdict.flags,
        "chosen_value": verdict.chosen_value,
        "sources": verdict.sources,
        "ts": now_utc().isoformat(),
    }
    try:
        await cache.set(
            QUALITY_KEY.format(ticker=ticker.upper()), payload, ttl=_QUALITY_TTL
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("quality_persist_failed", ticker=ticker, error=type(exc).__name__)
