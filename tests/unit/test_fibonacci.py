"""Unit tests for Fibonacci retracement calculations."""

import pytest
import pandas as pd

from src.quant.fibonacci import (
    calculate_fibonacci,
    RETRACEMENT_LEVELS,
    FibonacciLevels,
    format_fibonacci_message,
)


@pytest.fixture
def price_series_100_200() -> pd.Series:
    """Simple series with known 52W low=100, high=200."""
    return pd.Series([100.0 + i for i in range(101)])  # 100..200


@pytest.mark.unit
def test_fibonacci_high_low_detected(price_series_100_200):
    levels = calculate_fibonacci(price_series_100_200, ticker="TEST")
    assert levels.high_52w == pytest.approx(200.0)
    assert levels.low_52w == pytest.approx(100.0)


@pytest.mark.unit
def test_fibonacci_retracement_levels(price_series_100_200):
    """Verify retracement formula: high - (spread * ratio)."""
    levels = calculate_fibonacci(price_series_100_200, ticker="TEST")
    spread = 200.0 - 100.0  # 100
    expected_618 = 200.0 - (spread * 0.618)  # 138.2
    assert levels.retracements["61.8%"] == pytest.approx(expected_618, abs=0.01)


@pytest.mark.unit
def test_fibonacci_50_percent(price_series_100_200):
    """50% retracement should be exactly the midpoint."""
    levels = calculate_fibonacci(price_series_100_200, ticker="TEST")
    assert levels.retracements["50.0%"] == pytest.approx(150.0, abs=0.01)


@pytest.mark.unit
def test_fibonacci_all_retracement_keys(price_series_100_200):
    levels = calculate_fibonacci(price_series_100_200, ticker="TEST")
    for label in RETRACEMENT_LEVELS:
        assert label in levels.retracements, f"Missing retracement level: {label}"


@pytest.mark.unit
def test_fibonacci_zero_range_raises():
    flat = pd.Series([150.0] * 100)
    with pytest.raises(ValueError, match="No price range"):
        calculate_fibonacci(flat, ticker="FLAT")


@pytest.mark.unit
def test_fibonacci_insufficient_data():
    with pytest.raises(ValueError, match="at least 2 price points"):
        calculate_fibonacci(pd.Series([100.0]), ticker="ONE")


@pytest.mark.unit
def test_fibonacci_trend_detection():
    """Uptrend: series ending higher than earlier — should be UPTREND."""
    uptrend = pd.Series(range(100, 300), dtype=float)
    levels = calculate_fibonacci(uptrend, ticker="UP")
    assert levels.trend == "UPTREND"


@pytest.mark.unit
def test_fibonacci_support_below_current(price_series_100_200):
    levels = calculate_fibonacci(price_series_100_200, ticker="TEST")
    assert levels.nearest_support < levels.current_price


@pytest.mark.unit
def test_fibonacci_resistance_above_current(price_series_100_200):
    levels = calculate_fibonacci(price_series_100_200, ticker="TEST")
    assert levels.nearest_resistance > levels.current_price


@pytest.mark.unit
def test_fibonacci_format_message(price_series_100_200):
    levels = calculate_fibonacci(price_series_100_200, ticker="TEST")
    msg = format_fibonacci_message(levels)
    assert "TEST" in msg
    assert "52W" in msg
    assert "61.8%" in msg
