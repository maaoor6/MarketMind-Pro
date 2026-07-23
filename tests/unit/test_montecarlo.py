"""Unit tests for the Monte-Carlo robustness check."""

import pandas as pd
import pytest
from src.backtest.broker import Trade
from src.backtest.montecarlo import run_montecarlo


def _round_trip(ticker: str, buy: float, sell: float) -> list[Trade]:
    ts = pd.Timestamp("2020-01-01")
    qty = 10
    pnl = (sell - buy) * qty
    return [
        Trade(ticker, "buy", qty, buy, 0.0, "s", "r", ts),
        Trade(ticker, "sell", qty, sell, 0.0, "s", "r", ts, pnl=pnl),
    ]


@pytest.mark.unit
def test_montecarlo_none_when_too_few_trades():
    trades = _round_trip("AAA", 100, 110)
    assert run_montecarlo(trades, years=5.0) is None


@pytest.mark.unit
def test_montecarlo_positive_edge_is_robust():
    # 30 winning round trips (+10% each) → 5th-percentile CAGR must be positive.
    trades = []
    for i in range(30):
        trades += _round_trip(f"T{i}", 100, 110)
    result = run_montecarlo(trades, years=5.0, trials=500)
    assert result is not None
    assert result.trades_per_path == 30
    assert result.p5_cagr > 0
    assert result.prob_positive == 1.0


@pytest.mark.unit
def test_montecarlo_losing_edge_is_fragile():
    trades = []
    for i in range(30):
        trades += _round_trip(f"T{i}", 100, 90)  # −10% each
    result = run_montecarlo(trades, years=5.0, trials=500)
    assert result is not None
    assert result.p50_cagr < 0
    assert result.prob_positive == 0.0


@pytest.mark.unit
def test_montecarlo_is_deterministic_with_seed():
    trades = []
    for i in range(40):
        trades += _round_trip(f"T{i}", 100, 100 + (i % 5 - 2))  # mixed
    a = run_montecarlo(trades, years=5.0, trials=300, seed=7)
    b = run_montecarlo(trades, years=5.0, trials=300, seed=7)
    assert a.p50_cagr == b.p50_cagr
    assert a.p5_max_drawdown == b.p5_max_drawdown
