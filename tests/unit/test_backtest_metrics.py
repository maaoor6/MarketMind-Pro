"""Metric math on hand-built equity curves and trade logs."""

import pandas as pd
import pytest
from src.backtest.broker import Trade
from src.backtest.metrics import compute_metrics, per_ticker_breakdown, significance


def _trade(
    action: str,
    pnl: float | None = None,
    ticker: str = "TEST",
    strategy: str = "momentum_daily",
    price: float = 100.0,
    qty: int = 10,
    date: str = "2020-06-01",
) -> Trade:
    return Trade(
        ticker=ticker,
        action=action,
        quantity=qty,
        fill_price=price,
        commission=2.5,
        strategy=strategy,
        reason="test",
        bar_date=pd.Timestamp(date),
        pnl=pnl,
    )


@pytest.mark.unit
def test_total_return_and_cagr() -> None:
    index = pd.bdate_range("2020-01-01", periods=253 * 2)
    equity = pd.Series(
        [10_000 * (1.21 ** (i / (len(index) - 1))) for i in range(len(index))],
        index=index,
    )
    m = compute_metrics(equity, [])
    assert m.total_return_pct == pytest.approx(21.0, abs=0.01)
    # 21% over ~2 years ≈ 10% CAGR
    assert m.cagr_pct == pytest.approx(10.0, abs=0.5)
    assert m.final_equity == pytest.approx(12_100, rel=1e-4)


@pytest.mark.unit
def test_max_drawdown() -> None:
    index = pd.bdate_range("2020-01-01", periods=4)
    equity = pd.Series([100.0, 120.0, 90.0, 110.0], index=index)
    m = compute_metrics(equity, [])
    assert m.max_drawdown_pct == pytest.approx(-25.0)  # 120 → 90


@pytest.mark.unit
def test_win_rate_and_profit_factor() -> None:
    index = pd.bdate_range("2020-01-01", periods=3)
    equity = pd.Series([100.0, 101.0, 102.0], index=index)
    trades = [
        _trade("buy"),
        _trade("sell", pnl=50.0),
        _trade("buy"),
        _trade("sell", pnl=-25.0),
    ]
    m = compute_metrics(equity, trades)
    assert m.win_rate == pytest.approx(0.5)
    assert m.profit_factor == pytest.approx(2.0)
    assert m.closed_trades == 2
    assert m.trade_count == 4


@pytest.mark.unit
def test_avg_holding_days() -> None:
    index = pd.bdate_range("2020-01-01", periods=3)
    equity = pd.Series([100.0, 101.0, 102.0], index=index)
    trades = [
        _trade("buy", date="2020-01-01"),
        _trade("sell", pnl=1.0, date="2020-01-11"),
    ]
    m = compute_metrics(equity, trades)
    assert m.avg_holding_days == pytest.approx(10.0)


@pytest.mark.unit
def test_significance_needs_enough_trades() -> None:
    trades = []
    for _ in range(5):
        trades += [_trade("buy"), _trade("sell", pnl=10.0)]
    t_stat, label = significance(trades)
    assert "אין מספיק" in label


@pytest.mark.unit
def test_significance_detects_consistent_edge() -> None:
    trades = []
    for i in range(40):
        trades += [_trade("buy"), _trade("sell", pnl=10.0 + (i % 3))]
    t_stat, label = significance(trades)
    assert t_stat > 1.96
    assert label.startswith("✅")


@pytest.mark.unit
def test_significance_flags_noise() -> None:
    trades = []
    for i in range(40):
        pnl = 10.0 if i % 2 == 0 else -10.5
        trades += [_trade("buy"), _trade("sell", pnl=pnl)]
    _, label = significance(trades)
    assert label.startswith("❌")


@pytest.mark.unit
def test_per_ticker_breakdown_groups_and_sorts() -> None:
    trades = [
        _trade("sell", pnl=100.0, ticker="AAA"),
        _trade("sell", pnl=-20.0, ticker="AAA"),
        _trade("sell", pnl=300.0, ticker="BBB", strategy="breakout"),
    ]
    df = per_ticker_breakdown(trades)
    assert list(df.columns) == ["strategy", "ticker", "pnl", "trades", "win_rate"]
    assert df.iloc[0]["ticker"] == "BBB"
    aaa = df[df["ticker"] == "AAA"].iloc[0]
    assert aaa["pnl"] == pytest.approx(80.0)
    assert aaa["trades"] == 2
    assert aaa["win_rate"] == pytest.approx(0.5)
