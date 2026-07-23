"""Historical screener replay: sorting, filters, pinning, and the cap."""

import pandas as pd
import pytest
from src.backtest.universe_sim import simulate_universe
from src.trading.stockarena_client import Portfolio, Position
from src.utils.config import settings


def _row(
    close: float, pct: float, volume: float = 5e6, avg_vol: float = 5e6
) -> pd.Series:
    return pd.Series(
        {"Close": close, "pct_change_1d": pct, "Volume": volume, "avg_vol_3mo": avg_vol}
    )


def _portfolio(positions: dict[str, Position] | None = None) -> Portfolio:
    return Portfolio(
        cash=10_000.0, total_value=10_000.0, return_pct=0.0, positions=positions or {}
    )


@pytest.mark.unit
def test_spy_and_held_positions_pinned_first() -> None:
    held = {"ZZZ": Position(ticker="ZZZ", quantity=5, avg_price=50.0)}
    day = {
        "SPY": _row(400.0, 0.1),
        "AAA": _row(50.0, 3.0),
        "ZZZ": _row(50.0, -1.0),
    }
    universe = simulate_universe(day, _portfolio(held))
    assert universe[:2] == ["SPY", "ZZZ"]
    assert "AAA" in universe


@pytest.mark.unit
def test_gainers_ranked_before_losers() -> None:
    day = {"SPY": _row(400.0, 0.0)}
    for i, pct in enumerate((-3.0, 5.0, 1.0)):
        day[f"T{i}"] = _row(50.0, pct)
    universe = simulate_universe(day, _portfolio())
    # After pinned SPY: biggest gainer first (T1 +5%), then the rest.
    assert universe[0] == "SPY"
    assert universe[1] == "T1"


@pytest.mark.unit
def test_quality_filters_applied() -> None:
    day = {
        "SPY": _row(400.0, 0.0),
        "PENNY": _row(settings.trading_min_price - 1, 9.0),
        "ILLIQ": _row(50.0, 8.0, avg_vol=settings.trading_min_avg_volume - 1),
        "PRICY": _row(10_000.0 * settings.max_position_pct + 1, 7.0),
        "GOOD": _row(50.0, 6.0),
    }
    universe = simulate_universe(day, _portfolio())
    assert "PENNY" not in universe
    assert "ILLIQ" not in universe
    assert "PRICY" not in universe  # can't buy one share within the position cap
    assert "GOOD" in universe


@pytest.mark.unit
def test_capped_at_universe_size() -> None:
    day = {"SPY": _row(400.0, 0.0)}
    for i in range(40):
        day[f"T{i:02d}"] = _row(50.0, float(i))
    universe = simulate_universe(day, _portfolio())
    assert len(universe) == settings.trading_universe_size
    assert universe[0] == "SPY"


@pytest.mark.unit
def test_nan_pct_change_excluded() -> None:
    day = {"SPY": _row(400.0, 0.0), "NEW": _row(50.0, float("nan"))}
    universe = simulate_universe(day, _portfolio())
    assert "NEW" not in universe
