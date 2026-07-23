"""In-memory strategy allocator mirroring the live StrategyAllocator loop.

Signals are recorded per bar, scored once their horizon (in trading bars)
matures, and rolled into last-20 averages that drive ``compute_weights`` —
the same math the live agent uses, without PostgreSQL/Redis.
"""

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from src.trading.allocator import (
    _MIN_SCORED_FOR_WEIGHT,
    _RECENT_WINDOW,
    compute_weights,
)
from src.trading.strategies import Action, StrategySignal

# Live horizons are hours of wall-clock time; in a daily-bar simulation they
# map to trading days: 24h → 1, 72h → 3, 120h (5d) → 5, 168h (1wk) → 5,
# 480h (20d) → 20. Unknown horizons fall back to max(1, hours // 24).
HORIZON_BARS: dict[int, int] = {24: 1, 72: 3, 120: 5, 168: 5, 480: 20}


@dataclass
class PendingSignal:
    strategy: str
    ticker: str
    action: str  # "BUY" | "SELL"
    decision_price: float
    matures_at_bar: int
    regime: str | None = None


@dataclass
class ScoredSignal:
    strategy: str
    ticker: str
    virtual_return_pct: float
    bar: int
    regime: str | None = None


class InMemoryAllocator:
    """Tracks virtual strategy performance during a simulation."""

    def __init__(
        self, strategy_names: list[str], horizon_hours: dict[str, int]
    ) -> None:
        self._strategy_names = list(strategy_names)
        self._horizon_bars = {
            name: HORIZON_BARS.get(hours, max(1, hours // 24))
            for name, hours in horizon_hours.items()
        }
        self._pending: list[PendingSignal] = []
        self._recent: dict[str, deque[float]] = {
            name: deque(maxlen=_RECENT_WINDOW) for name in self._strategy_names
        }
        # Per-regime recents — mirrors the live get_weights(regime) behavior
        # where regime-conditional evidence overrides the global average.
        self._recent_by_regime: dict[str, dict[str, deque[float]]] = {}
        self.scored: list[ScoredSignal] = []

    def record(self, sig: StrategySignal, bar: int, regime: str | None = None) -> None:
        """Register a non-HOLD signal for scoring after its horizon."""
        if sig.action == Action.HOLD or sig.price <= 0:
            return
        horizon = self._horizon_bars.get(sig.strategy, 1)
        self._pending.append(
            PendingSignal(
                strategy=sig.strategy,
                ticker=sig.ticker,
                action=sig.action.value,
                decision_price=sig.price,
                matures_at_bar=bar + horizon,
                regime=regime,
            )
        )

    def score_matured(
        self, bar: int, price_lookup: Callable[[str], float | None]
    ) -> int:
        """Score signals whose horizon has passed. Returns count scored."""
        still_pending: list[PendingSignal] = []
        count = 0
        for pending in self._pending:
            if pending.matures_at_bar > bar:
                still_pending.append(pending)
                continue
            price = price_lookup(pending.ticker)
            if price is None or price <= 0:
                still_pending.append(pending)  # fail-closed: retry next bar
                continue
            direction = 1.0 if pending.action == "BUY" else -1.0
            ret = (
                (price - pending.decision_price)
                / pending.decision_price
                * 100
                * direction
            )
            self._recent[pending.strategy].append(ret)
            if pending.regime is not None:
                bucket = self._recent_by_regime.setdefault(
                    pending.regime,
                    {n: deque(maxlen=_RECENT_WINDOW) for n in self._strategy_names},
                )
                bucket[pending.strategy].append(ret)
            self.scored.append(
                ScoredSignal(
                    strategy=pending.strategy,
                    ticker=pending.ticker,
                    virtual_return_pct=ret,
                    bar=bar,
                    regime=pending.regime,
                )
            )
            count += 1
        self._pending = still_pending
        return count

    def avg_returns(self, regime: str | None = None) -> dict[str, float | None]:
        """Last-window average per strategy; None below the scoring minimum.

        With ``regime``, the regime-conditional average is preferred per
        strategy (falling back to the global one below the minimum).
        """
        regime_recent = self._recent_by_regime.get(regime) if regime else None
        out: dict[str, float | None] = {}
        for name in self._strategy_names:
            recent = self._recent[name]
            if regime_recent is not None:
                conditioned = regime_recent[name]
                if len(conditioned) >= _MIN_SCORED_FOR_WEIGHT:
                    recent = conditioned
            out[name] = (
                sum(recent) / len(recent)
                if len(recent) >= _MIN_SCORED_FOR_WEIGHT
                else None
            )
        return out

    def weights(self, regime: str | None = None) -> dict[str, float]:
        """Current capital weights, exactly as the live agent computes them."""
        return compute_weights(self.avg_returns(regime), self._strategy_names)

    def seed_averages(self, seeds: dict[str, float]) -> None:
        """Pre-fill recent returns from prior-run averages (walk-forward)."""
        for name, avg in seeds.items():
            if name in self._recent and avg is not None:
                self._recent[name].extend([avg] * _MIN_SCORED_FOR_WEIGHT)
