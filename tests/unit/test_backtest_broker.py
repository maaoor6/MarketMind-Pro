"""SimulatedBroker cash/commission math and Portfolio marking."""

import pandas as pd
import pytest
from src.backtest.broker import SimulatedBroker
from src.trading.risk import OrderPlan

_DATE = pd.Timestamp("2020-06-01")


def _plan(action: str, ticker: str = "TEST", qty: int = 10) -> OrderPlan:
    return OrderPlan(
        action=action,
        ticker=ticker,
        quantity=qty,
        est_price=100.0,
        strategy="momentum_daily",
        reason="test",
    )


@pytest.fixture
def broker() -> SimulatedBroker:
    return SimulatedBroker(cash=10_000.0, commission=2.5, slippage_bps=0.0)


@pytest.mark.unit
def test_buy_debits_cash_and_opens_position(broker: SimulatedBroker) -> None:
    trade = broker.execute(_plan("buy"), market_price=100.0, bar_date=_DATE)
    assert trade is not None
    assert broker.cash == pytest.approx(10_000 - 10 * 100 - 2.5)
    pos = broker.positions["TEST"]
    assert pos.quantity == 10
    assert pos.avg_price == pytest.approx(100.0)


@pytest.mark.unit
def test_buy_averages_price_on_add(broker: SimulatedBroker) -> None:
    broker.execute(_plan("buy"), market_price=100.0, bar_date=_DATE)
    broker.execute(_plan("buy"), market_price=120.0, bar_date=_DATE)
    pos = broker.positions["TEST"]
    assert pos.quantity == 20
    assert pos.avg_price == pytest.approx(110.0)


@pytest.mark.unit
def test_sell_realizes_pnl_net_of_round_trip_fees(broker: SimulatedBroker) -> None:
    broker.execute(_plan("buy"), market_price=100.0, bar_date=_DATE)
    trade = broker.execute(_plan("sell"), market_price=110.0, bar_date=_DATE)
    assert trade is not None
    # 10 shares * $10 gain - $2.5 sell fee - $2.5 buy fee
    assert trade.pnl == pytest.approx(100 - 2.5 - 2.5)
    assert "TEST" not in broker.positions
    assert broker.cash == pytest.approx(10_000 - 1000 - 2.5 + 1100 - 2.5)


@pytest.mark.unit
def test_buy_rejected_on_insufficient_cash() -> None:
    broker = SimulatedBroker(cash=500.0, commission=2.5, slippage_bps=0.0)
    assert broker.execute(_plan("buy"), market_price=100.0, bar_date=_DATE) is None
    assert broker.cash == 500.0
    assert broker.positions == {}


@pytest.mark.unit
def test_sell_rejected_without_position(broker: SimulatedBroker) -> None:
    assert broker.execute(_plan("sell"), market_price=100.0, bar_date=_DATE) is None


@pytest.mark.unit
def test_slippage_moves_fill_against_the_trade() -> None:
    broker = SimulatedBroker(cash=100_000.0, commission=0.0, slippage_bps=10.0)
    buy = broker.execute(_plan("buy"), market_price=100.0, bar_date=_DATE)
    assert buy is not None
    assert buy.fill_price == pytest.approx(100.10)
    sell = broker.execute(_plan("sell"), market_price=100.0, bar_date=_DATE)
    assert sell is not None
    assert sell.fill_price == pytest.approx(99.90)


@pytest.mark.unit
def test_mark_returns_live_shaped_portfolio(broker: SimulatedBroker) -> None:
    broker.execute(_plan("buy"), market_price=100.0, bar_date=_DATE)
    portfolio = broker.mark({"TEST": 105.0})
    assert portfolio.cash == pytest.approx(broker.cash)
    assert portfolio.total_value == pytest.approx(broker.cash + 10 * 105.0)
    assert portfolio.positions["TEST"].current_price == pytest.approx(105.0)
    assert portfolio.positions["TEST"].market_value == pytest.approx(1050.0)
    assert portfolio.return_pct == pytest.approx(
        (portfolio.total_value - 10_000) / 10_000 * 100
    )
