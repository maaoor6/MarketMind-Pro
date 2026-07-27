"""ExitEngine and circuit-breaker rules (in-memory RiskManager mirror)."""

import pytest
from src.backtest.risk_sim import ExitEngine, circuit_breaker_tripped
from src.trading.stockarena_client import Portfolio, Position
from src.trading.strategies import StrategyContext
from src.utils.config import settings


def _portfolio(avg_price: float, quantity: int = 10) -> Portfolio:
    pos = Position(
        ticker="TEST", quantity=quantity, avg_price=avg_price, current_price=avg_price
    )
    return Portfolio(
        cash=5_000.0,
        total_value=5_000.0 + quantity * avg_price,
        return_pct=0.0,
        positions={"TEST": pos},
    )


def _ctx(price: float, support: float | None = None) -> StrategyContext:
    return StrategyContext(
        ticker="TEST",
        signals={"price": price},
        fibonacci=(
            {
                "high_52w": price * 1.5,
                "low_52w": price * 0.5,
                "trend": "UPTREND",
                "nearest_support": support,
                "nearest_resistance": price * 1.2,
            }
            if support is not None
            else None
        ),
        weekly={},
        monthly={},
        momentum=None,
        position=None,
    )


@pytest.mark.unit
def test_stop_loss_fires_at_threshold() -> None:
    engine = ExitEngine()
    price = 100 * (1 - settings.stop_loss_pct)
    plans = engine.check_exits(_portfolio(100.0), {"TEST": _ctx(price)})
    assert len(plans) == 1
    assert plans[0].action == "sell"
    assert "stop-loss" in plans[0].reason


@pytest.mark.unit
def test_take_profit_requires_commission_drag() -> None:
    engine = ExitEngine()
    # Exactly +10% is NOT enough — the round-trip fee shifts the bar up.
    plans = engine.check_exits(
        _portfolio(100.0), {"TEST": _ctx(100 * (1 + settings.take_profit_pct))}
    )
    assert plans == []
    drag = (2 * settings.trade_commission) / (100.0 * 10)
    price = 100 * (1 + settings.take_profit_pct + drag + 0.001)
    plans = engine.check_exits(_portfolio(100.0), {"TEST": _ctx(price)})
    assert len(plans) == 1
    assert "take-profit" in plans[0].reason


@pytest.mark.unit
def test_trailing_stop_arms_then_fires() -> None:
    engine = ExitEngine()
    portfolio = _portfolio(100.0)
    # Run up to +10% (below take-profit + drag) — HWM=110.
    assert engine.check_exits(portfolio, {"TEST": _ctx(110.0)}) == []
    # Pull back 4% below HWM while still above the arming threshold
    # (change +5.6% >= take_profit/2 + drag = 5.5%).
    price = 110.0 * (1 - settings.trail_stop_pct)
    plans = engine.check_exits(portfolio, {"TEST": _ctx(price)})
    assert len(plans) == 1
    assert "trailing stop" in plans[0].reason


@pytest.mark.unit
def test_trailing_stop_not_armed_near_breakeven() -> None:
    engine = ExitEngine()
    portfolio = _portfolio(100.0)
    assert engine.check_exits(portfolio, {"TEST": _ctx(102.0)}) == []
    # 4% below HWM but position only ~-2% — not armed, no exit.
    assert engine.check_exits(portfolio, {"TEST": _ctx(97.9)}) == []


@pytest.mark.unit
def test_hwm_reset_after_exit() -> None:
    engine = ExitEngine()
    portfolio = _portfolio(100.0)
    engine.check_exits(portfolio, {"TEST": _ctx(108.0)})
    engine.clear_position_state("TEST")
    # Fresh position: old HWM of 108 must not trigger an instant exit.
    assert engine.check_exits(_portfolio(103.0), {"TEST": _ctx(103.0)}) == []


@pytest.mark.unit
def test_fib_support_break_exits() -> None:
    engine = ExitEngine()
    plans = engine.check_exits(_portfolio(100.0), {"TEST": _ctx(98.0, support=99.5)})
    assert len(plans) == 1
    assert "support" in plans[0].reason


@pytest.mark.unit
def test_circuit_breaker_threshold() -> None:
    assert not circuit_breaker_tripped(
        10_000, 10_000 * (1 - settings.max_daily_loss_pct + 0.001)
    )
    assert circuit_breaker_tripped(10_000, 10_000 * (1 - settings.max_daily_loss_pct))
    assert not circuit_breaker_tripped(0.0, 9_000)
