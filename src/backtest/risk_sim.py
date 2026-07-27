"""In-memory mirrors of the Redis-backed RiskManager rules.

Every rule below is a literal port of :class:`src.trading.risk.RiskManager`
— stop-loss, take-profit, trailing stop (high-water mark), Fibonacci support
break, and the daily circuit breaker — with plain dicts replacing Redis.
"""

from src.trading.risk import OrderPlan, RiskManager
from src.trading.stockarena_client import Portfolio
from src.trading.strategies import StrategyContext
from src.utils.config import settings


class ExitEngine:
    """Protective exits with per-position high-water marks kept in memory."""

    def __init__(self) -> None:
        self._hwm: dict[str, float] = {}
        # ticker → strategy that opened the position (exit-rule routing);
        # mirror of Redis trading:entry_strategy:{ticker}.
        self.entry_strategy: dict[str, str] = {}

    def check_exits(
        self, portfolio: Portfolio, contexts: dict[str, StrategyContext]
    ) -> list[OrderPlan]:
        """Stop-loss / take-profit / trailing-stop / Fib-break full exits."""
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
            drag = RiskManager._commission_drag_pct(pos.avg_price * pos.quantity)
            reason: str | None = None

            if change <= -settings.stop_loss_pct:
                reason = f"stop-loss: {change:+.1%} from avg ${pos.avg_price:.2f}"
            elif change >= settings.take_profit_pct + drag and (
                RiskManager.profit_cap_applies(self.entry_strategy.get(ticker))
            ):
                reason = f"take-profit cap: {change:+.1%} (net of fees)"
            else:
                trail_reason = self._check_trailing_stop(ticker, price, change, drag)
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

    def _check_trailing_stop(
        self, ticker: str, price: float, change: float, drag: float
    ) -> str | None:
        hwm = self._hwm.get(ticker, price)
        if price > hwm:
            hwm = price
        self._hwm[ticker] = hwm

        armed = change >= settings.take_profit_pct / 2 + drag
        if armed and price <= hwm * (1 - settings.trail_stop_pct):
            return (
                f"trailing stop: {settings.trail_stop_pct:.0%} below "
                f"high-water mark ${hwm:.2f}"
            )
        return None

    def clear_position_state(self, ticker: str) -> None:
        """Reset the high-water mark and entry strategy after a full exit."""
        self._hwm.pop(ticker, None)
        self.entry_strategy.pop(ticker, None)


def circuit_breaker_tripped(day_open_value: float, current_value: float) -> bool:
    """Daily-bar approximation of the live intraday circuit breaker.

    Live compares against the day's opening portfolio value; with daily bars
    the previous close stands in for the day open.
    """
    if day_open_value <= 0:
        return False
    drawdown = (current_value - day_open_value) / day_open_value
    return drawdown <= -settings.max_daily_loss_pct
