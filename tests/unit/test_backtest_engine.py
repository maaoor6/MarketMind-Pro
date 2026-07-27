"""Unit tests for the backtest engine's anti-churn gate."""

import pandas as pd
import pytest
from src.backtest.engine import _Simulation, run_full_agent
from src.backtest.features import precompute_features
from src.trading.risk import OrderPlan
from src.trading.strategies import Action, Strategy
from src.utils.config import settings


def make_sim(bars: int = 10) -> _Simulation:
    index = pd.bdate_range("2024-01-01", periods=bars)
    df = pd.DataFrame(
        {
            "Open": [100.0] * bars,
            "High": [101.0] * bars,
            "Low": [99.0] * bars,
            "Close": [100.0] * bars,
            "Volume": [1e6] * bars,
        },
        index=index,
    )
    return _Simulation({"AAPL": df}, cash=10_000.0, slippage_bps=0.0, vix_close=None)


def buy_plan() -> OrderPlan:
    return OrderPlan(
        action="buy",
        ticker="AAPL",
        quantity=10,
        est_price=100.0,
        strategy="test",
        reason="test",
    )


@pytest.mark.unit
def test_strategy_sell_blocked_during_min_holding():
    sim = make_sim()
    sim.queue(buy_plan())
    sim.fill_pending(sim.calendar[0], bar=0)
    assert "AAPL" in sim.broker.positions
    assert sim.entry_bar["AAPL"] == 0
    assert sim.strategy_sell_allowed("AAPL", bar=0) is False
    assert sim.strategy_sell_allowed("AAPL", bar=sim.min_holding_bars - 1) is False
    assert sim.strategy_sell_allowed("AAPL", bar=sim.min_holding_bars) is True


@pytest.mark.unit
def test_strategy_sell_allowed_without_entry_record():
    # Fail-open: positions with no recorded entry (e.g. pre-existing) may sell.
    sim = make_sim()
    assert sim.strategy_sell_allowed("AAPL", bar=0) is True


@pytest.mark.unit
def test_sell_fill_clears_entry_bar():
    sim = make_sim()
    sim.queue(buy_plan())
    sim.fill_pending(sim.calendar[0], bar=0)
    sim.queue(
        OrderPlan(
            action="sell",
            ticker="AAPL",
            quantity=10,
            est_price=100.0,
            strategy="risk_exit",
            reason="test",
        )
    )
    sim.fill_pending(sim.calendar[5], bar=5)
    assert "AAPL" not in sim.broker.positions
    assert "AAPL" not in sim.entry_bar
    assert sim.strategy_sell_allowed("AAPL", bar=5) is True


@pytest.mark.unit
def test_min_holding_bars_derived_from_settings():
    sim = make_sim()
    assert sim.min_holding_bars == max(1, settings.min_holding_hours // 24)


# ── evidence-ranked buys ───────────────────────────────────────────────


class _RankStub(Strategy):
    """Buys every ticker; confidence differs per ticker (AAA strongest)."""

    name = "rank_stub"
    timeframe = "1d"
    eval_horizon_hours = 24

    def evaluate(self, ctx):
        if ctx.ticker == "SPY" or ctx.price is None:
            return self._hold(ctx, "skip")
        conf = 0.9 if ctx.ticker == "AAA" else 0.6
        return self._signal(ctx, Action.BUY, conf, "stub buy")


def _trend_frame(bars: int = 300) -> pd.DataFrame:
    index = pd.bdate_range("2020-01-01", periods=bars)
    closes = (
        pd.Series(100.0, index=index)
        + pd.Series(range(bars), index=index, dtype=float) * 0.1
    )
    return pd.DataFrame(
        {
            "Open": closes.values,
            "High": closes.values * 1.01,
            "Low": closes.values * 0.99,
            "Close": closes.values,
            "Volume": [1e6] * bars,
        },
        index=index,
    )


@pytest.mark.unit
def test_buys_execute_in_confidence_rank_order():
    features = {
        "SPY": precompute_features(_trend_frame()),
        "BBB": precompute_features(_trend_frame()),
        "AAA": precompute_features(_trend_frame()),
    }
    result = run_full_agent(
        features,
        cash=10_000.0,
        slippage_bps=0.0,
        strategies=[_RankStub()],
        dynamic_universe=False,
    )
    buys = [t for t in result.trades if t.action == "buy"]
    assert buys, "stub strategy should have produced buys"
    # AAA (confidence 0.9) must be queued and filled before BBB (0.6), even
    # though BBB precedes it in the dict iteration order.
    assert buys[0].ticker == "AAA"


@pytest.mark.unit
def test_strategy_sell_waits_for_entry_horizon():
    sim = make_sim(bars=40)
    sim.queue(buy_plan())
    sim.fill_pending(sim.calendar[0], bar=0)
    # Opened by a 480h-horizon strategy → 20-bar minimum, not the 3-bar floor.
    sim.exits.entry_strategy["AAPL"] = "ts_momentum"
    assert sim.strategy_sell_allowed("AAPL", bar=sim.min_holding_bars) is False
    assert sim.strategy_sell_allowed("AAPL", bar=19) is False
    assert sim.strategy_sell_allowed("AAPL", bar=20) is True
    # A short-horizon opener keeps the flat floor.
    sim.exits.entry_strategy["AAPL"] = "momentum_daily"
    assert sim.strategy_sell_allowed("AAPL", bar=sim.min_holding_bars) is True
