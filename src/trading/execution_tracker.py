"""Execution drift tracker — live fills vs. the decision price.

The backtest fills at a theoretical price; live, StockArena fills at a moving
market price, so real returns can bleed to slippage/latency. This tracker records
the *adverse* drift on every fill (positive = worse than the decision price for
that side) and keeps a running aggregate in Redis so the weekly report can show
the average drift — an early warning that execution is eroding the edge.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.database.cache import cache
from src.utils.logger import get_logger

logger = get_logger(__name__)

_DRIFT_KEY = "trading:exec_drift"


@dataclass
class DriftSummary:
    count: int
    avg_adverse_pct: float  # mean signed slippage, + = worse than decision price
    worst_adverse_pct: float


def adverse_drift_pct(requested: float, filled: float, action: str) -> float | None:
    """Signed slippage as % of the decision price (+ = unfavorable fill)."""
    if requested <= 0 or filled <= 0:
        return None
    if action.lower() == "buy":
        return (filled - requested) / requested * 100  # paid more = bad
    return (requested - filled) / requested * 100  # sold lower = bad


class ExecutionTracker:
    """Accumulates per-fill execution drift into a Redis running aggregate."""

    async def record(self, requested: float, filled: float, action: str) -> None:
        drift = adverse_drift_pct(requested, filled, action)
        if drift is None:
            return
        try:
            state = await cache.get(_DRIFT_KEY) or {}
            count = int(state.get("count", 0)) + 1
            total = float(state.get("sum_adverse", 0.0)) + drift
            worst = max(float(state.get("worst_adverse", drift)), drift)
            await cache.set(
                _DRIFT_KEY,
                {"count": count, "sum_adverse": total, "worst_adverse": worst},
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("exec_drift_record_failed", error=str(exc))

    @staticmethod
    async def summary() -> DriftSummary | None:
        try:
            state = await cache.get(_DRIFT_KEY)
        except Exception as exc:  # noqa: BLE001
            logger.debug("exec_drift_summary_failed", error=str(exc))
            return None
        if not state or not state.get("count"):
            return None
        count = int(state["count"])
        return DriftSummary(
            count=count,
            avg_adverse_pct=float(state["sum_adverse"]) / count,
            worst_adverse_pct=float(state.get("worst_adverse", 0.0)),
        )
