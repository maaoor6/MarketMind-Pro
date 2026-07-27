"""Unit tests for the Phase-2B candidate strategies (DualMomentum, VolTargetTrend)."""

from src.trading.agents import _ROLE_MAP
from src.trading.stockarena_client import Position
from src.trading.strategies import (
    Action,
    DualMomentum,
    StrategyContext,
    VolTargetTrend,
    experimental_strategies,
)

_HELD = Position(ticker="X", quantity=10, avg_price=100.0, current_price=100.0)


def _ctx(
    price=200.0,
    sma50=190.0,
    sma200=180.0,
    ret_12m=None,
    ret_3m=None,
    vol_20d=None,
    position=None,
):
    return StrategyContext(
        ticker="AAPL",
        signals={
            "price": price,
            "ret_12m": ret_12m,
            "ret_3m": ret_3m,
            "vol_20d": vol_20d,
            "moving_averages": {"SMA_50": sma50, "SMA_200": sma200},
        },
        fibonacci=None,
        weekly={},
        monthly={},
        momentum=None,
        position=position,
    )


# ── DualMomentum ─────────────────────────────────────────────────────────────


def test_dual_momentum_buys_positive_dual():
    sig = DualMomentum().evaluate(_ctx(ret_12m=0.3, ret_3m=0.1))
    assert sig.action == Action.BUY
    assert sig.confidence > 0.5


def test_dual_momentum_holds_negative_recency():
    sig = DualMomentum().evaluate(_ctx(ret_12m=0.3, ret_3m=-0.05))
    assert sig.action == Action.HOLD


def test_dual_momentum_sells_on_lost_absolute():
    sig = DualMomentum().evaluate(_ctx(ret_12m=-0.1, ret_3m=0.02, position=_HELD))
    assert sig.action == Action.SELL


def test_dual_momentum_failclosed_missing_data():
    assert DualMomentum().evaluate(_ctx(ret_12m=None)).action == Action.HOLD


# ── VolTargetTrend ───────────────────────────────────────────────────────────


def test_vol_target_buys_calm_uptrend():
    sig = VolTargetTrend().evaluate(_ctx(vol_20d=0.15))
    assert sig.action == Action.BUY


def test_vol_target_holds_when_vol_high():
    sig = VolTargetTrend().evaluate(_ctx(vol_20d=0.45))
    assert sig.action == Action.HOLD


def test_vol_target_sells_below_sma200():
    sig = VolTargetTrend().evaluate(_ctx(price=170.0, vol_20d=0.15, position=_HELD))
    assert sig.action == Action.SELL


def test_vol_target_failclosed_missing_data():
    assert VolTargetTrend().evaluate(_ctx(vol_20d=None)).action == Action.HOLD


# ── Registration ─────────────────────────────────────────────────────────────


def test_new_strategies_are_experimental_only():
    names = {s.name for s in experimental_strategies()}
    assert "dual_momentum" in names
    assert "vol_target_trend" in names


def test_new_strategies_have_roles():
    assert _ROLE_MAP["dual_momentum"] == "core_momentum"
    assert _ROLE_MAP["vol_target_trend"] == "low_vol_quality"
