"""Performance metrics for backtest equity curves and trade logs."""

import math
from dataclasses import dataclass

import pandas as pd

from src.backtest.broker import Trade

_TRADING_DAYS = 252
_MIN_TRADES_FOR_SIGNIFICANCE = 30
_T_CRITICAL = 1.96  # two-sided 5%


@dataclass
class Metrics:
    """Summary statistics for one equity curve + its closed trades."""

    total_return_pct: float
    cagr_pct: float
    sharpe: float
    sortino: float
    max_drawdown_pct: float
    win_rate: float | None  # None when no closed trades
    profit_factor: float | None
    trade_count: int
    closed_trades: int
    avg_holding_days: float | None
    exposure_pct: float
    final_equity: float


def compute_metrics(
    equity: pd.Series, trades: list[Trade], exposure: pd.Series | None = None
) -> Metrics:
    """Compute all summary metrics.

    Args:
        equity: Daily portfolio value indexed by date.
        trades: Simulated fills (buys and sells).
        exposure: Optional daily fraction of equity held in positions.
    """
    equity = equity.dropna()
    if len(equity) < 2:
        raise ValueError("equity curve needs at least 2 points")
    start_value = float(equity.iloc[0])
    final_value = float(equity.iloc[-1])
    total_return = (final_value - start_value) / start_value * 100

    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1 / 365.25)
    cagr = ((final_value / start_value) ** (1 / years) - 1) * 100

    daily = equity.pct_change().dropna()
    std = float(daily.std())
    sharpe = float(daily.mean()) / std * math.sqrt(_TRADING_DAYS) if std > 0 else 0.0
    downside = daily[daily < 0]
    downside_std = float(downside.std()) if len(downside) > 1 else 0.0
    sortino = (
        float(daily.mean()) / downside_std * math.sqrt(_TRADING_DAYS)
        if downside_std > 0
        else 0.0
    )

    running_max = equity.cummax()
    max_drawdown = float(((equity - running_max) / running_max).min()) * 100

    sells = [t for t in trades if t.action == "sell" and t.pnl is not None]
    wins = [t for t in sells if t.pnl > 0]
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in sells if t.pnl <= 0)
    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > 0
        else (None if not sells else float("inf"))
    )

    avg_holding = _avg_holding_days(trades)
    exposure_pct = float(exposure.mean()) * 100 if exposure is not None else 0.0

    return Metrics(
        total_return_pct=total_return,
        cagr_pct=cagr,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown_pct=max_drawdown,
        win_rate=len(wins) / len(sells) if sells else None,
        profit_factor=profit_factor,
        trade_count=len(trades),
        closed_trades=len(sells),
        avg_holding_days=avg_holding,
        exposure_pct=exposure_pct,
        final_equity=final_value,
    )


def _avg_holding_days(trades: list[Trade]) -> float | None:
    """Average calendar days between first buy and each closing sell, per ticker."""
    open_since: dict[str, pd.Timestamp] = {}
    spans: list[float] = []
    for trade in trades:
        if trade.action == "buy":
            open_since.setdefault(trade.ticker, trade.bar_date)
        else:
            opened = open_since.pop(trade.ticker, None)
            if opened is not None:
                spans.append((trade.bar_date - opened).days)
    return sum(spans) / len(spans) if spans else None


def trade_returns_pct(trades: list[Trade]) -> list[float]:
    """Per-closed-trade return as % of the position's cost basis."""
    returns: list[float] = []
    cost_basis: dict[str, float] = {}
    for trade in trades:
        notional = trade.quantity * trade.fill_price
        if trade.action == "buy":
            cost_basis[trade.ticker] = cost_basis.get(trade.ticker, 0.0) + notional
        elif trade.pnl is not None:
            basis = cost_basis.pop(trade.ticker, notional)
            if basis > 0:
                returns.append(trade.pnl / basis * 100)
    return returns


def significance(trades: list[Trade]) -> tuple[float, str]:
    """t-statistic on closed-trade returns + a plain-language verdict.

    Returns:
        (t_stat, label) where label is one of the Hebrew report tags.
    """
    returns = trade_returns_pct(trades)
    n = len(returns)
    if n < 2:
        return 0.0, "⚠️ עוד אין מספיק עסקאות לקבוע"
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std = math.sqrt(var)
    t_stat = mean / (std / math.sqrt(n)) if std > 0 else 0.0
    if n < _MIN_TRADES_FOR_SIGNIFICANCE:
        return t_stat, "⚠️ עוד אין מספיק עסקאות לקבוע"
    if t_stat > _T_CRITICAL:
        return t_stat, "✅ יתרון מובהק"
    return t_stat, "❌ לא מובהק — ייתכן מזל"


def per_ticker_breakdown(trades: list[Trade]) -> pd.DataFrame:
    """Realized P&L and win rate grouped by (strategy, ticker)."""
    rows: list[dict] = []
    for trade in trades:
        if trade.action == "sell" and trade.pnl is not None:
            rows.append(
                {"strategy": trade.strategy, "ticker": trade.ticker, "pnl": trade.pnl}
            )
    if not rows:
        return pd.DataFrame(columns=["strategy", "ticker", "pnl", "trades", "win_rate"])
    df = pd.DataFrame(rows)
    grouped = df.groupby(["strategy", "ticker"])["pnl"]
    out = grouped.agg(pnl="sum", trades="count").reset_index()
    out["win_rate"] = grouped.apply(lambda s: (s > 0).mean()).to_numpy()
    return out.sort_values("pnl", ascending=False).reset_index(drop=True)


def attribute_pnl(trades: list[Trade]) -> dict[str, dict]:
    """Realized P&L per ENTRY strategy — sells credited to the opener.

    Protective exits carry strategy="risk_exit" on the sell, so attribution
    walks the trades chronologically: the first buy of a position defines
    the opening strategy, and every sell's realized ``pnl`` (net of fees)
    is credited to that opener.
    """
    entry: dict[str, str] = {}
    out: dict[str, dict] = {}
    for trade in trades:
        if trade.action == "buy":
            entry.setdefault(trade.ticker, trade.strategy)
            continue
        if trade.action != "sell" or trade.pnl is None:
            continue
        opener = entry.pop(trade.ticker, trade.strategy)
        bucket = out.setdefault(
            opener, {"realized_pnl": 0.0, "round_trips": 0, "wins": 0}
        )
        bucket["realized_pnl"] += float(trade.pnl)
        bucket["round_trips"] += 1
        if trade.pnl > 0:
            bucket["wins"] += 1
    return out
