"""Unit tests for the Phase-1 new-method strategies.

SectorRotation, CrossAsset and Seasonality are cross-sectional / calendar
strategies, so they need context fields the shared ``test_strategies`` helper
does not set (ticker, ``as_of``, ``ret_3m``, ``cross_section``). This module
carries its own minimal builder.
"""

from datetime import date

import pytest
from src.trading.stockarena_client import Position
from src.trading.strategies import (
    Action,
    CrossAsset,
    Seasonality,
    SectorRotation,
    StrategyContext,
    experimental_strategies,
)

_HELD = Position(ticker="X", quantity=10, avg_price=100.0, current_price=100.0)


def ctx(
    ticker: str,
    price: float = 200.0,
    sma200: float | None = 180.0,
    *,
    ret_3m: float | None = None,
    ret_6m: float | None = None,
    monthly_bullish: bool | None = True,
    position: Position | None = None,
    as_of: date | None = date(2026, 1, 15),
    cross_section: dict | None = None,
) -> StrategyContext:
    return StrategyContext(
        ticker=ticker,
        signals={
            "price": price,
            "ret_3m": ret_3m,
            "ret_6m": ret_6m,
            "moving_averages": {"SMA_200": sma200},
        },
        fibonacci=None,
        weekly={"macd_bullish": True},
        monthly={"macd_bullish": monthly_bullish},
        momentum=None,
        position=position,
        as_of=as_of,
        cross_section=cross_section,
    )


# ── SectorRotation ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_sector_rotation_ignores_non_sector_ticker():
    assert SectorRotation().evaluate(ctx("AAPL", ret_3m=0.1)).action == Action.HOLD


@pytest.mark.unit
def test_sector_rotation_buys_leader_by_rank():
    sig = SectorRotation().evaluate(
        ctx("XLK", ret_3m=-0.01, cross_section={"sector_rank": {"XLK": 0.9}})
    )
    assert sig.action == Action.BUY


@pytest.mark.unit
def test_sector_rotation_rank_below_threshold_holds():
    sig = SectorRotation().evaluate(
        ctx("XLK", ret_3m=0.1, cross_section={"sector_rank": {"XLK": 0.5}})
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_sector_rotation_absolute_fallback_buys_on_momentum():
    # No cross-section injected → uses the sector's own 3-month momentum.
    assert SectorRotation().evaluate(ctx("XLF", ret_3m=0.08)).action == Action.BUY


@pytest.mark.unit
def test_sector_rotation_sells_when_below_sma200():
    sig = SectorRotation().evaluate(
        ctx("XLF", price=100, sma200=110, ret_3m=0.02, position=_HELD)
    )
    assert sig.action == Action.SELL


@pytest.mark.unit
def test_sector_rotation_holds_on_missing_data():
    assert SectorRotation().evaluate(ctx("XLK", ret_3m=None)).action == Action.HOLD


# ── CrossAsset ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cross_asset_ignores_equities():
    assert CrossAsset().evaluate(ctx("AAPL", ret_6m=0.1)).action == Action.HOLD


@pytest.mark.unit
def test_cross_asset_buys_bond_uptrend():
    assert CrossAsset().evaluate(ctx("TLT", ret_6m=0.05)).action == Action.BUY


@pytest.mark.unit
def test_cross_asset_no_buy_when_monthly_bearish():
    sig = CrossAsset().evaluate(ctx("GLD", ret_6m=0.05, monthly_bullish=False))
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_cross_asset_sells_on_broken_trend():
    sig = CrossAsset().evaluate(
        ctx("TLT", price=90, sma200=100, ret_6m=-0.02, position=_HELD)
    )
    assert sig.action == Action.SELL


# ── Seasonality ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_seasonality_buys_turn_of_month_in_uptrend():
    sig = Seasonality().evaluate(ctx("AAPL", as_of=date(2026, 6, 30)))
    assert sig.action == Action.BUY
    assert "turn-of-month" in sig.reason


@pytest.mark.unit
def test_seasonality_buys_favorable_month_in_uptrend():
    sig = Seasonality().evaluate(ctx("AAPL", as_of=date(2026, 1, 15)))
    assert sig.action == Action.BUY


@pytest.mark.unit
def test_seasonality_holds_unfavorable_midmonth():
    sig = Seasonality().evaluate(ctx("AAPL", as_of=date(2026, 7, 15)))
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_seasonality_skips_downtrend():
    sig = Seasonality().evaluate(ctx("AAPL", price=150, sma200=180))
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_seasonality_trims_into_weak_season_when_held():
    sig = Seasonality().evaluate(ctx("AAPL", as_of=date(2026, 5, 2), position=_HELD))
    assert sig.action == Action.SELL


@pytest.mark.unit
def test_seasonality_holds_without_date():
    assert Seasonality().evaluate(ctx("AAPL", as_of=None)).action == Action.HOLD


# ── Registration ───────────────────────────────────────────────────────


@pytest.mark.unit
def test_new_strategies_registration():
    from src.trading.strategies import default_strategies

    live = {s.name for s in default_strategies()}
    experimental = {s.name for s in experimental_strategies()}
    # All three new-method strategies stay experimental — sector_rotation +
    # cross_asset were promoted 2026-07-23 then reverted (walk-forward regressed).
    assert {"sector_rotation", "cross_asset", "seasonality"} <= experimental
    assert not ({"sector_rotation", "cross_asset", "seasonality"} & live)
