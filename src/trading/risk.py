"""Risk manager — position sizing, exit rules, cooldowns, circuit breaker.

All thresholds come from Settings so they are tunable via env without code
changes. Sizing/validation math is pure and synchronous; Redis is used only
for cooldowns, trailing-stop high-water marks, and the daily circuit breaker.
"""

from dataclasses import dataclass
from datetime import datetime

from src.database.cache import cache
from src.trading.stockarena_client import Portfolio
from src.trading.strategies import (
    STRATEGY_HORIZON_HOURS,
    Action,
    StrategyContext,
    StrategySignal,
)
from src.utils.config import settings
from src.utils.logger import get_logger
from src.utils.timezone_utils import now_us

logger = get_logger(__name__)

_COOLDOWN_KEY = "trading:cooldown:{ticker}"
_HWM_KEY = "trading:hwm:{ticker}"
_ENTRY_TIME_KEY = "trading:entry_time:{ticker}"
_ENTRY_STRATEGY_KEY = "trading:entry_strategy:{ticker}"
_PORTFOLIO_HWM_KEY = "trading:portfolio_hwm"
_DAY_OPEN_KEY = "trading:day_open_value:{day}"
_HALT_KEY = "trading:halted_until:{day}"


@dataclass
class OrderPlan:
    action: str  # "buy" | "sell"
    ticker: str
    quantity: int
    est_price: float
    strategy: str
    reason: str


class RiskManager:
    """Enforces every risk limit before an order may be placed."""

    @staticmethod
    def _commission_drag_pct(notional: float) -> float:
        """Round-trip commission (buy + sell) as a fraction of the notional."""
        if notional <= 0:
            return 1.0
        return (2 * settings.trade_commission) / notional

    # Long-horizon entries ride the trailing stop only — a hard +10% cap
    # would chop exactly the winners (ts_momentum, low_vol_trend, high_52w)
    # that earn their return by letting profits run.
    _LONG_HORIZON_HOURS = 480

    @classmethod
    def profit_cap_applies(cls, entry_strategy: str | None) -> bool:
        """False when the position's opening strategy has a ≥480h horizon."""
        if not entry_strategy:
            return True
        horizon = STRATEGY_HORIZON_HOURS.get(entry_strategy, 0)
        return horizon < cls._LONG_HORIZON_HOURS

    # ── Exits (run before any new entries) ─────────────────────────────

    async def check_exits(
        self, portfolio: Portfolio, contexts: dict[str, StrategyContext]
    ) -> list[OrderPlan]:
        """Stop-loss / trailing-stop / take-profit / Fib-break full exits."""
        plans: list[OrderPlan] = []
        for ticker, pos in portfolio.positions.items():
            ctx = contexts.get(ticker)
            price = (
                ctx.price
                if ctx and ctx.price
                else (pos.current_price if pos.current_price else None)
            )
            if price is None or pos.avg_price <= 0 or pos.quantity < 1:
                continue

            change = (price - pos.avg_price) / pos.avg_price
            # Profit exits must clear the round-trip commission, so thresholds
            # shift up by the commission's share of the position. Protective
            # exits (stop-loss, support break) fire regardless of fees.
            drag = self._commission_drag_pct(pos.avg_price * pos.quantity)
            entry_strategy = await self.get_entry_strategy(ticker)
            reason: str | None = None

            if change <= -settings.stop_loss_pct:
                reason = f"stop-loss: {change:+.1%} from avg ${pos.avg_price:.2f}"
            elif change >= settings.take_profit_pct + drag and self.profit_cap_applies(
                entry_strategy
            ):
                reason = f"take-profit cap: {change:+.1%} (net of fees)"
            else:
                trail_reason = await self._check_trailing_stop(
                    ticker, price, change, drag
                )
                if trail_reason:
                    reason = trail_reason
                elif ctx and ctx.fibonacci:
                    support = ctx.fibonacci.get("nearest_support")
                    if support is not None and price < support * 0.99:
                        reason = f"Fibonacci support ${support:.2f} broken"

            if reason:
                plans.append(
                    OrderPlan(
                        action="sell",
                        ticker=ticker,
                        quantity=pos.quantity,
                        est_price=price,
                        strategy="risk_exit",
                        reason=reason,
                    )
                )
        return plans

    async def _check_trailing_stop(
        self, ticker: str, price: float, change: float, drag: float = 0.0
    ) -> str | None:
        """Track per-position high-water mark; exit on trail-stop breach.

        Only arms once the position is up at least half the take-profit
        threshold plus commission drag, so small wiggles around break-even
        (which would net a loss after fees) don't trigger it.
        """
        key = _HWM_KEY.format(ticker=ticker)
        hwm = await cache.get(key)
        hwm = float(hwm) if hwm is not None else price
        if price > hwm:
            hwm = price
        await cache.set(key, hwm, ttl=86400 * 30)

        armed = change >= settings.take_profit_pct / 2 + drag
        if armed and price <= hwm * (1 - settings.trail_stop_pct):
            return (
                f"trailing stop: {settings.trail_stop_pct:.0%} below "
                f"high-water mark ${hwm:.2f}"
            )
        return None

    async def clear_position_state(self, ticker: str) -> None:
        """Reset high-water mark, entry time and entry strategy after an exit."""
        await cache.delete(_HWM_KEY.format(ticker=ticker))
        await cache.delete(_ENTRY_TIME_KEY.format(ticker=ticker))
        await cache.delete(_ENTRY_STRATEGY_KEY.format(ticker=ticker))

    # ── Entry attribution (which strategy opened the position) ─────────

    async def set_entry_strategy(self, ticker: str, strategy: str) -> None:
        """Remember which strategy opened the position (exit-rule routing)."""
        await cache.set(
            _ENTRY_STRATEGY_KEY.format(ticker=ticker), strategy, ttl=86400 * 30
        )

    async def get_entry_strategy(self, ticker: str) -> str | None:
        raw = await cache.get(_ENTRY_STRATEGY_KEY.format(ticker=ticker))
        return str(raw) if raw else None

    # ── Portfolio drawdown brake ───────────────────────────────────────

    async def drawdown_brake_factor(self, portfolio: Portfolio) -> float:
        """Buy-budget multiplier based on drawdown from the all-time HWM.

        Below ``drawdown_brake_pct`` from the portfolio's high-water mark,
        new buys are scaled by ``drawdown_size_factor`` — stops the death
        spiral where a losing streak keeps deploying full-size bets.
        Protective exits are never affected.
        """
        value = portfolio.total_value
        if value <= 0:
            return 1.0
        raw = await cache.get(_PORTFOLIO_HWM_KEY)
        hwm = float(raw) if raw is not None else value
        if raw is None or value > hwm:
            hwm = max(hwm, value)
            await cache.set(_PORTFOLIO_HWM_KEY, hwm)
        drawdown = (hwm - value) / hwm if hwm > 0 else 0.0
        if drawdown >= settings.drawdown_brake_pct:
            return settings.drawdown_size_factor
        return 1.0

    # ── Minimum holding period (anti-churn) ────────────────────────────

    async def set_entry_time(self, ticker: str) -> None:
        """Record when a position was opened (called after a filled buy)."""
        await cache.set(
            _ENTRY_TIME_KEY.format(ticker=ticker),
            now_us().isoformat(),
            ttl=86400 * 30,
        )

    @staticmethod
    def min_holding_hours_for(entry_strategy: str | None) -> int:
        """Minimum holding period, scaled to the opening strategy's horizon.

        A flat 72h floor let short-term strategies dump ts_momentum's
        month-long positions after 3 days — attribution showed the agent's
        best standalone strategy losing the most inside the combination
        (3,193 premature round trips). Each entry now holds at least as
        long as its own evaluation horizon.
        """
        base = settings.min_holding_hours
        if not entry_strategy:
            return base
        return max(base, STRATEGY_HORIZON_HOURS.get(entry_strategy, 0))

    async def in_min_holding(self, ticker: str) -> bool:
        """True while the position is younger than its minimum holding time.

        Protective exits (check_exits) never consult this — it only gates
        strategy SELL signals, so a fresh entry can't be dumped the next day
        by a different strategy (the churn that killed the backtest agent).
        Fail-open: with no recorded entry time the sell is allowed.
        """
        raw = await cache.get(_ENTRY_TIME_KEY.format(ticker=ticker))
        if raw is None:
            return False
        try:
            entered = datetime.fromisoformat(str(raw))
        except ValueError:
            return False
        age_hours = (now_us() - entered).total_seconds() / 3600
        entry_strategy = await self.get_entry_strategy(ticker)
        return age_hours < self.min_holding_hours_for(entry_strategy)

    # ── Daily circuit breaker ──────────────────────────────────────────

    async def circuit_breaker_tripped(self, portfolio: Portfolio) -> bool:
        """True when today's drawdown exceeds max_daily_loss_pct — halt today."""
        day = now_us().strftime("%Y-%m-%d")
        halt_key = _HALT_KEY.format(day=day)
        if await cache.get(halt_key):
            return True

        open_key = _DAY_OPEN_KEY.format(day=day)
        day_open = await cache.get(open_key)
        if day_open is None:
            await cache.set(open_key, portfolio.total_value, ttl=86400 * 2)
            return False

        day_open = float(day_open)
        if day_open <= 0:
            return False
        drawdown = (portfolio.total_value - day_open) / day_open
        if drawdown <= -settings.max_daily_loss_pct:
            await cache.set(halt_key, "1", ttl=86400)
            logger.warning(
                "daily_circuit_breaker_tripped",
                drawdown=round(drawdown, 4),
                day_open=day_open,
                total_value=portfolio.total_value,
            )
            return True
        return False

    # ── Entries ────────────────────────────────────────────────────────

    def size_buy(
        self,
        sig: StrategySignal,
        weight: float,
        portfolio: Portfolio,
        extended_hours: bool = False,
        drawdown_factor: float = 1.0,
    ) -> OrderPlan | None:
        """Turn a BUY signal into a sized order, or None if limits forbid it.

        Args:
            drawdown_factor: Budget multiplier from ``drawdown_brake_factor``.
        """
        if sig.action != Action.BUY or sig.price <= 0:
            return None
        floor_notional = settings.min_trade_notional
        min_conf = (
            settings.extended_hours_min_confidence
            if extended_hours
            else settings.min_confidence
        )
        if sig.confidence < min_conf:
            return None

        position = portfolio.positions.get(sig.ticker)
        position_value = 0.0
        if position is not None:
            position_value = position.market_value or (
                position.quantity * (position.current_price or position.avg_price)
            )
        if position is None and len(portfolio.positions) >= settings.max_open_positions:
            return None

        position_room = (
            settings.max_position_pct * portfolio.total_value - position_value
        )
        spendable = (
            portfolio.cash
            - settings.cash_reserve_pct * portfolio.total_value
            - settings.trade_commission  # buy fee is paid from cash
        )
        budget = min(position_room, spendable) * weight * sig.confidence
        # Volatility-aware sizing: shrink bets on wild names so every position
        # carries similar dollar risk. Fail-open when vol data is missing.
        if sig.volatility and sig.volatility > settings.target_position_vol:
            budget *= settings.target_position_vol / sig.volatility
        # ATR equal-dollar-at-risk cap: an adverse atr_stop_multiple × ATR move
        # must risk no more than position_risk_pct of the portfolio. Volatile
        # names (high atr_pct) get a smaller notional. Fail-open without ATR.
        if (
            settings.atr_sizing_enabled
            and sig.atr_pct
            and sig.atr_pct > 0
            and portfolio.total_value > 0
        ):
            stop_distance = settings.atr_stop_multiple * sig.atr_pct
            risk_cap = (
                settings.position_risk_pct * portfolio.total_value / stop_distance
            )
            budget = min(budget, risk_cap)
        # Drawdown brake: smaller bets while the portfolio digs out of a hole.
        budget *= drawdown_factor
        if extended_hours:
            budget *= settings.extended_hours_size_factor
        if budget < floor_notional:
            return None

        quantity = int(budget // sig.price)
        if quantity < 1:
            return None
        notional = quantity * sig.price
        if (
            notional + settings.trade_commission > portfolio.cash
            or notional < floor_notional
        ):
            return None
        # Skip trades where the round-trip fee eats more than max_commission_pct.
        if self._commission_drag_pct(notional) > settings.max_commission_pct:
            return None

        return OrderPlan(
            action="buy",
            ticker=sig.ticker,
            quantity=quantity,
            est_price=sig.price,
            strategy=sig.strategy,
            reason=sig.reason,
        )

    def validate_sell(
        self, sig: StrategySignal, portfolio: Portfolio
    ) -> OrderPlan | None:
        """Full exit of held shares only — never short."""
        if sig.action != Action.SELL:
            return None
        position = portfolio.positions.get(sig.ticker)
        if position is None or position.quantity < 1:
            return None
        return OrderPlan(
            action="sell",
            ticker=sig.ticker,
            quantity=position.quantity,
            est_price=sig.price or (position.current_price or position.avg_price),
            strategy=sig.strategy,
            reason=sig.reason,
        )

    # ── Cooldowns ──────────────────────────────────────────────────────

    async def in_cooldown(self, ticker: str) -> bool:
        return await cache.get(_COOLDOWN_KEY.format(ticker=ticker)) is not None

    async def set_cooldown(self, ticker: str) -> None:
        await cache.set(
            _COOLDOWN_KEY.format(ticker=ticker),
            "1",
            ttl=settings.ticker_cooldown_minutes * 60,
        )
