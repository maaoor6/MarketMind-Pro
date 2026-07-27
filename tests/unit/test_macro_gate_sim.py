"""Unit tests for the backtest macro-gate replay."""

import pandas as pd
import pytest
from src.backtest.macro_gate_sim import build_macro_states


def _spy_frame(index, close, sma200, sma50) -> pd.DataFrame:
    return pd.DataFrame(
        {"Close": close, "SMA_200": sma200, "SMA_50": sma50}, index=index
    )


@pytest.mark.unit
def test_build_macro_states_bull_day_is_on():
    idx = pd.to_datetime(["2020-01-02", "2020-01-03"])
    spy = _spy_frame(idx, [100, 102], [90, 90], [95, 95])
    vix = pd.Series([13.0, 14.0], index=idx)
    states = build_macro_states({"SPY": spy}, vix)
    assert states[idx[0]][0] == "ON"


@pytest.mark.unit
def test_build_macro_states_crisis_day_is_off():
    idx = pd.to_datetime(["2020-03-16", "2020-03-17"])
    # Price below SMA200 and VIX blown out → OFF.
    spy = _spy_frame(idx, [80, 78], [100, 100], [95, 95])
    vix = pd.Series([60.0, 55.0], index=idx)
    states = build_macro_states({"SPY": spy}, vix)
    assert states[idx[0]][0] == "OFF"
    assert states[idx[0]][1] == 0.0


@pytest.mark.unit
def test_build_macro_states_no_spy_is_empty():
    assert build_macro_states({}, None) == {}


@pytest.mark.unit
def test_build_macro_states_missing_vix_is_neutral():
    idx = pd.to_datetime(["2020-01-02"])
    spy = _spy_frame(idx, [100], [90], [95])
    states = build_macro_states({"SPY": spy}, None)
    # No VIX → volatility neutral, but bull trend keeps it ON.
    assert states[idx[0]][0] == "ON"
