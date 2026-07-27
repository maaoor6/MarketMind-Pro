"""Shadow / paper-mode pipeline — the mandatory gate before live promotion.

A strategy that wins the walk-forward backtest is NOT promoted straight into
``default_strategies()``. It first runs in **shadow mode** for
``shadow_mode_days`` (default 30): the Orchestrator evaluates it read-only,
records its signals against real-time forward data, but never sizes or executes
them. Promotion requires the shadow window to elapse AND no concept drift
(checked by the Phase-2C drift monitor).

Shadow membership is config-driven (``SHADOW_STRATEGY_NAMES``) so promoting a
walk-forward winner into shadow is a one-line change, and demoting is instant.
State lives in Redis (``trading:shadow:{name}``) — stateless agents, per the
architecture rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.database.cache import cache
from src.trading.strategies import (
    Action,
    Strategy,
    StrategyContext,
    experimental_strategies,
)
from src.utils.config import settings
from src.utils.logger import get_logger
from src.utils.timezone_utils import now_utc

logger = get_logger(__name__)

SHADOW_KEY = "trading:shadow:{name}"
_SHADOW_TTL = 90 * 86400  # keep a shadow record well past the window
_MAX_SIGNALS = 50  # cap the recorded signal log per strategy


def shadow_strategy_names() -> set[str]:
    """The experimental strategy names currently in shadow mode (from config)."""
    raw = getattr(settings, "shadow_strategy_names", "") or ""
    return {n.strip() for n in raw.split(",") if n.strip()}


def shadow_strategies() -> list[Strategy]:
    """The experimental strategy instances currently in shadow mode."""
    names = shadow_strategy_names()
    if not names:
        return []
    return [s for s in experimental_strategies() if s.name in names]


@dataclass
class ShadowRecord:
    """Per-strategy shadow state for display + promotion checks."""

    strategy: str
    entered_at: str | None
    signal_count: int
    days_in_shadow: float | None
    ready: bool


class ShadowBook:
    """Records shadow-strategy signals read-only and tracks promotion readiness."""

    def __init__(self, strategies: list[Strategy] | None = None) -> None:
        self._strategies = strategies if strategies is not None else shadow_strategies()

    @property
    def strategy_names(self) -> set[str]:
        return {s.name for s in self._strategies}

    async def observe(self, contexts: dict[str, StrategyContext]) -> None:
        """Evaluate every shadow strategy read-only and record non-HOLD signals."""
        for strategy in self._strategies:
            for ctx in contexts.values():
                try:
                    sig = strategy.evaluate(ctx)
                except (
                    Exception
                ) as exc:  # noqa: BLE001 — a shadow eval must never break the cycle
                    logger.debug(
                        "shadow_eval_failed",
                        strategy=strategy.name,
                        error=type(exc).__name__,
                    )
                    continue
                if sig.action != Action.HOLD:
                    await self._record(strategy.name, sig)

    async def _record(self, name: str, sig) -> None:
        key = SHADOW_KEY.format(name=name)
        try:
            state = await cache.get(key)
        except Exception:  # noqa: BLE001
            state = None
        now = now_utc().isoformat()
        if not isinstance(state, dict):
            state = {"entered_at": now, "signal_count": 0, "signals": []}
        state["signal_count"] = int(state.get("signal_count", 0)) + 1
        signals = state.get("signals") or []
        signals.append(
            {
                "ticker": sig.ticker,
                "action": str(sig.action),
                "price": sig.price,
                "ts": now,
            }
        )
        state["signals"] = signals[-_MAX_SIGNALS:]
        try:
            await cache.set(key, state, ttl=_SHADOW_TTL)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "shadow_record_failed", strategy=name, error=type(exc).__name__
            )

    async def _entered_at(self, name: str) -> datetime | None:
        try:
            state = await cache.get(SHADOW_KEY.format(name=name))
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(state, dict) or not state.get("entered_at"):
            return None
        try:
            return datetime.fromisoformat(state["entered_at"])
        except (TypeError, ValueError):
            return None

    async def days_in_shadow(
        self, name: str, now: datetime | None = None
    ) -> float | None:
        entered = await self._entered_at(name)
        if entered is None:
            return None
        ref = now or now_utc()
        if entered.tzinfo is None:
            ref = ref.replace(tzinfo=None)
        return (ref - entered).total_seconds() / 86400.0

    async def promotion_ready(self, name: str, now: datetime | None = None) -> bool:
        """True once the shadow window has fully elapsed.

        Drift-freedom is verified separately by the Phase-2C drift monitor; this
        only checks the elapsed-time gate.
        """
        days = await self.days_in_shadow(name, now)
        return days is not None and days >= settings.shadow_mode_days

    async def status(self, now: datetime | None = None) -> list[ShadowRecord]:
        """Per-strategy shadow status for the Telegram cockpit / reports."""
        out: list[ShadowRecord] = []
        for strategy in self._strategies:
            name = strategy.name
            try:
                state = await cache.get(SHADOW_KEY.format(name=name))
            except Exception:  # noqa: BLE001
                state = None
            entered = state.get("entered_at") if isinstance(state, dict) else None
            count = int(state.get("signal_count", 0)) if isinstance(state, dict) else 0
            days = await self.days_in_shadow(name, now)
            out.append(
                ShadowRecord(
                    strategy=name,
                    entered_at=entered,
                    signal_count=count,
                    days_in_shadow=round(days, 1) if days is not None else None,
                    ready=(days is not None and days >= settings.shadow_mode_days),
                )
            )
        return out
