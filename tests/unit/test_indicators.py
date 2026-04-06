"""Unit tests for technical indicators — known input/output pairs."""

import numpy as np
import pandas as pd
import pytest
from src.quant.indicators import all_moving_averages, ema, macd, rsi, sma, volume_spike

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def simple_prices() -> pd.Series:
    """10 price values: steady climb."""
    return pd.Series([10.0, 11.0, 12.0, 11.5, 13.0, 14.0, 13.5, 15.0, 16.0, 17.0])


@pytest.fixture
def flat_prices() -> pd.Series:
    return pd.Series([100.0] * 30)


@pytest.fixture
def rsi_prices() -> pd.Series:
    """Known RSI test data — alternating up/down days."""
    return pd.Series(
        [
            44.34,
            44.09,
            44.15,
            43.61,
            44.33,
            44.83,
            45.10,
            45.15,
            43.61,
            44.33,
            44.83,
            45.10,
            45.15,
            45.89,
            46.23,
            46.08,
            45.89,
            46.03,
            45.61,
            46.28,
        ]
    )


@pytest.fixture
def volume_data() -> pd.Series:
    return pd.Series([1_000_000] * 8 + [2_500_000, 3_000_000])  # spikes at end


# ── SMA ───────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_sma_basic(simple_prices):
    result = sma(simple_prices, 3)
    assert result.isna().sum() == 2  # First 2 values are NaN (min_periods=3)
    assert result.iloc[2] == pytest.approx((10 + 11 + 12) / 3)


@pytest.mark.unit
def test_sma_period_1(simple_prices):
    result = sma(simple_prices, 1)
    pd.testing.assert_series_equal(result, simple_prices)


@pytest.mark.unit
def test_sma_flat(flat_prices):
    result = sma(flat_prices, 10)
    assert (result.dropna() == 100.0).all()


@pytest.mark.unit
def test_sma_invalid_period(simple_prices):
    with pytest.raises(ValueError, match="period must be >= 1"):
        sma(simple_prices, 0)


# ── EMA ───────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_ema_basic(simple_prices):
    result = ema(simple_prices, 3)
    assert result.isna().sum() == 2  # min_periods=3
    assert len(result) == len(simple_prices)


@pytest.mark.unit
def test_ema_flat(flat_prices):
    result = ema(flat_prices, 20)
    valid = result.dropna()
    assert all(abs(v - 100.0) < 1e-6 for v in valid)


@pytest.mark.unit
def test_ema_faster_than_sma_on_uptrend(simple_prices):
    """EMA should react faster than SMA to recent price moves."""
    ema_result = ema(simple_prices, 3).dropna()
    sma_result = sma(simple_prices, 3).dropna()
    # EMA should be higher than SMA in an uptrend at the end
    assert float(ema_result.iloc[-1]) >= float(sma_result.iloc[-1])


# ── RSI ───────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_rsi_bounds(rsi_prices):
    result = rsi(rsi_prices)
    valid = result.values.dropna()
    assert (valid >= 0).all() and (valid <= 100).all()


@pytest.mark.unit
def test_rsi_all_up():
    """Prices only going up → RSI should approach 100."""
    prices = pd.Series(range(1, 51), dtype=float)
    result = rsi(prices, 14)
    assert float(result.values.dropna().iloc[-1]) > 90


@pytest.mark.unit
def test_rsi_all_down():
    """Prices only going down → RSI should approach 0."""
    prices = pd.Series(range(50, 0, -1), dtype=float)
    result = rsi(prices, 14)
    assert float(result.values.dropna().iloc[-1]) < 10


@pytest.mark.unit
def test_rsi_overbought_oversold(rsi_prices):
    result = rsi(rsi_prices)
    assert result.is_overbought().dtype == bool
    assert result.is_oversold().dtype == bool


@pytest.mark.unit
def test_rsi_invalid_period(simple_prices):
    with pytest.raises(ValueError):
        rsi(simple_prices, 0)


# ── MACD ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_macd_components():
    prices = pd.Series(range(1, 101), dtype=float)
    result = macd(prices)
    assert len(result.macd_line) == 100
    assert len(result.signal_line) == 100
    assert len(result.histogram) == 100


@pytest.mark.unit
def test_macd_histogram_equals_macd_minus_signal():
    prices = pd.Series(range(1, 101), dtype=float)
    result = macd(prices)
    expected_hist = result.macd_line - result.signal_line
    pd.testing.assert_series_equal(result.histogram, expected_hist)


@pytest.mark.unit
def test_macd_uptrend():
    """In a strong uptrend, MACD line should be positive."""
    prices = pd.Series([float(i**1.5) for i in range(1, 101)])
    result = macd(prices)
    valid = result.macd_line.dropna()
    assert float(valid.iloc[-1]) > 0


# ── Volume Spike ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_volume_spike_detects_spike(volume_data):
    result = volume_spike(volume_data, ma_period=8, spike_multiplier=2.0)
    assert bool(result.iloc[-1]) is True
    assert bool(result.iloc[-2]) is True
    assert bool(result.iloc[0]) is False


@pytest.mark.unit
def test_volume_spike_no_spike():
    flat_vol = pd.Series([1_000_000.0] * 15)
    result = volume_spike(flat_vol, ma_period=10, spike_multiplier=2.0)
    assert result.dropna().sum() == 0


# ── All Moving Averages ───────────────────────────────────────────────


@pytest.mark.unit
def test_all_moving_averages_keys():
    prices = pd.Series(range(1, 251), dtype=float)
    result = all_moving_averages(prices)
    expected_keys = {
        f"{t}_{p}" for t in ("SMA", "EMA") for p in (20, 50, 100, 150, 200)
    }
    assert set(result.keys()) == expected_keys


@pytest.mark.unit
def test_all_moving_averages_values_finite():
    prices = pd.Series(range(1, 251), dtype=float)
    result = all_moving_averages(prices)
    for name, series in result.items():
        assert np.isfinite(
            float(series.dropna().iloc[-1])
        ), f"{name} has non-finite last value"
