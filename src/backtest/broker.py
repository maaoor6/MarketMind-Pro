"""In-memory simulated broker — cash, positions, commissions, slippage.

Exposes the same :class:`Portfolio` / :class:`Position` dataclasses the live
StockArena client returns, so ``RiskManager.size_buy`` / ``validate_sell``
work unchanged against the simulation.
"""

from dataclasses import dataclass

import pandas as pd

from src.trading.risk import OrderPlan
from src.trading.stockarena_client import Portfolio, Position

DEFAULT_SLIPPAGE_BPS = 5.0  # 0.05% against the trade on every fill


@dataclass
class Trade:
    """One simulated fill."""

    ticker: str
    action: str  # "buy" | "sell"
    quantity: int
    fill_price: float
    commission: float
    strategy: str
    reason: str
    bar_date: pd.Timestamp
    pnl: float | None = None  # realized P&L, sells only (net of both fees)


class SimulatedBroker:
    """Tracks cash and positions; fills orders with commission + slippage."""

    def __init__(
        self,
        cash: float,
        commission: float,
        slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    ) -> None:
        self.cash = float(cash)
        self.initial_cash = float(cash)
        self.commission = float(commission)
        self.slippage_bps = float(slippage_bps)
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []
        # Buy-side commission per open position, so realized P&L on the
        # eventual sell accounts for the full round trip.
        self._entry_fees: dict[str, float] = {}

    def _slip(self, price: float, action: str) -> float:
        adverse = self.slippage_bps / 10_000
        return price * (1 + adverse) if action == "buy" else price * (1 - adverse)

    def execute(
        self, plan: OrderPlan, market_price: float, bar_date: pd.Timestamp
    ) -> Trade | None:
        """Fill an order at ``market_price`` (± slippage), or None if rejected."""
        if plan.quantity < 1 or market_price <= 0:
            return None
        fill = self._slip(market_price, plan.action)

        if plan.action == "buy":
            cost = plan.quantity * fill + self.commission
            if cost > self.cash:
                return None
            self.cash -= cost
            held = self.positions.get(plan.ticker)
            if held is None:
                self.positions[plan.ticker] = Position(
                    ticker=plan.ticker,
                    quantity=plan.quantity,
                    avg_price=fill,
                    current_price=fill,
                )
                self._entry_fees[plan.ticker] = self.commission
            else:
                total_qty = held.quantity + plan.quantity
                held.avg_price = (
                    held.avg_price * held.quantity + fill * plan.quantity
                ) / total_qty
                held.quantity = total_qty
                held.current_price = fill
                self._entry_fees[plan.ticker] = (
                    self._entry_fees.get(plan.ticker, 0.0) + self.commission
                )
            pnl = None
        else:
            held = self.positions.get(plan.ticker)
            if held is None or plan.quantity > held.quantity:
                return None
            proceeds = plan.quantity * fill - self.commission
            self.cash += proceeds
            entry_fee = self._entry_fees.get(plan.ticker, 0.0)
            pnl = (fill - held.avg_price) * plan.quantity - self.commission - entry_fee
            held.quantity -= plan.quantity
            if held.quantity == 0:
                del self.positions[plan.ticker]
                self._entry_fees.pop(plan.ticker, None)

        trade = Trade(
            ticker=plan.ticker,
            action=plan.action,
            quantity=plan.quantity,
            fill_price=fill,
            commission=self.commission,
            strategy=plan.strategy,
            reason=plan.reason,
            bar_date=bar_date,
            pnl=pnl,
        )
        self.trades.append(trade)
        return trade

    def mark(self, prices: dict[str, float]) -> Portfolio:
        """Mark positions to market and return a live-shaped Portfolio."""
        equity = self.cash
        for ticker, pos in self.positions.items():
            price = prices.get(ticker)
            if price is not None and price > 0:
                pos.current_price = float(price)
            mark_price = pos.current_price or pos.avg_price
            pos.market_value = pos.quantity * mark_price
            equity += pos.market_value
        return Portfolio(
            cash=self.cash,
            total_value=equity,
            return_pct=(equity - self.initial_cash) / self.initial_cash * 100,
            positions=self.positions,
        )
