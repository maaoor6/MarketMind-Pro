"""Unit tests for technical indicators — known input/output pairs."""

import numpy as np
import pandas as pd
import pytest
from src.quant.indicators import (
    MomentumScore,
    all_moving_averages,
    bollinger,
    ema,
    generate_signals,
    macd,
    momentum_score,
    rsi,
    sma,
    volume_spike,
)

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


# ── SMA 150 ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_sma_150_nan_before_150_bars():
    """SMA_150 must produce NaN for the first 149 values."""
    prices = pd.Series(range(1, 201), dtype=float)
    result = sma(prices, 150)
    assert result.isna().sum() == 149


@pytest.mark.unit
def test_sma_150_known_value():
    """SMA_150 of [1..200] at bar 150 (index 149) == mean(1..150) == 75.5."""
    prices = pd.Series(range(1, 201), dtype=float)
    result = sma(prices, 150)
    assert result.iloc[149] == pytest.approx(75.5)


@pytest.mark.unit
def test_sma_150_in_all_moving_averages():
    """all_moving_averages must include SMA_150 and EMA_150 with finite values."""
    prices = pd.Series(range(1, 251), dtype=float)
    result = all_moving_averages(prices)
    assert "SMA_150" in result
    assert "EMA_150" in result
    sma_150_last = float(result["SMA_150"].dropna().iloc[-1])
    ema_150_last = float(result["EMA_150"].dropna().iloc[-1])
    assert sma_150_last > 0
    assert ema_150_last > 0


# ── Momentum Score Tests ─────────────────────────────────────────────────────────


@pytest.fixture
def strong_signals() -> dict:
    """Signals dict representing a strongly trending stock."""
    return {
        "price": 210.0,
        "rsi": 65.0,
        "rsi_signal": "NEUTRAL",
        "macd_histogram": 0.5,
        "volume_spike": True,
        "moving_averages": {"SMA_200": 180.0},
    }


@pytest.fixture
def weak_signals() -> dict:
    """Signals dict representing a weak/falling stock."""
    return {
        "price": 90.0,
        "rsi": 28.0,
        "rsi_signal": "OVERSOLD",
        "macd_histogram": -0.3,
        "volume_spike": False,
        "moving_averages": {"SMA_200": 120.0},
    }


@pytest.mark.unit
def test_momentum_score_returns_dataclass(strong_signals):
    prices = pd.Series(range(1, 260), dtype=float)
    result = momentum_score(strong_signals, prices)
    assert isinstance(result, MomentumScore)
    assert 0 <= result.score <= 100
    assert result.label in {"Very Strong", "Strong", "Neutral", "Weak", "Very Weak"}
    assert result.emoji in {"🔥", "🟢", "🟡", "🟠", "🔴"}
    assert isinstance(result.breakdown, dict)


@pytest.mark.unit
def test_momentum_score_strong_stock(strong_signals):
    """Stock above SMA200, RSI in 50-70, MACD bullish, volume spike, 5d gain → high score."""
    prices = pd.Series(list(range(200, 260)), dtype=float)  # steady uptrend
    result = momentum_score(strong_signals, prices)
    # Expected: RSI 65→15, SMA200 above→20, MACD>0→20, spike→20, 5d>3%→20 = 95
    assert result.score >= 75
    assert result.label in {"Very Strong", "Strong"}


@pytest.mark.unit
def test_momentum_score_weak_stock(weak_signals):
    """Stock below SMA200, RSI<40, MACD bearish, no spike → low score."""
    prices = pd.Series(list(range(130, 70, -1)), dtype=float)  # steady downtrend
    result = momentum_score(weak_signals, prices)
    assert result.score <= 20
    assert result.label in {"Weak", "Very Weak"}


@pytest.mark.unit
def test_momentum_score_empty_prices(strong_signals):
    """Empty price series → 5d change component defaults to 0 but no crash."""
    result = momentum_score(strong_signals, pd.Series(dtype=float))
    assert isinstance(result, MomentumScore)
    assert result.breakdown["5d_change"] == 0


@pytest.mark.unit
def test_momentum_score_breakdown_sums_to_total(strong_signals):
    prices = pd.Series(range(1, 260), dtype=float)
    result = momentum_score(strong_signals, prices)
    assert sum(result.breakdown.values()) == result.score


@pytest.mark.unit
def test_momentum_score_all_components_present(strong_signals):
    prices = pd.Series(range(1, 260), dtype=float)
    result = momentum_score(strong_signals, prices)
    for key in ("rsi", "sma200", "macd", "volume", "5d_change"):
        assert key in result.breakdown


@pytest.mark.unit
def test_momentum_score_very_strong_label():
    """All 5 components at max → score 100, Very Strong."""
    # prices[-6] = 150, price = 210 → +40% 5d change → 20 pts
    prices = pd.Series(
        [100.0, 120.0, 130.0, 140.0, 145.0, 150.0, 155.0, 160.0, 180.0, 200.0, 210.0]
    )
    signals = {
        "price": 210.0,  # current price (above SMA200=100)
        "rsi": 75.0,  # > 70 → 20 pts
        "rsi_signal": "OVERBOUGHT",
        "macd_histogram": 1.0,  # > 0 → 20 pts
        "volume_spike": True,  # → 20 pts
        "moving_averages": {"SMA_200": 100.0},  # price 210 > 100 → 20 pts
    }
    result = momentum_score(signals, prices)
    assert result.score == 100
    assert result.label == "Very Strong"
    assert result.emoji == "🔥"


@pytest.mark.unit
def test_momentum_score_from_generate_signals():
    """Verify momentum_score works end-to-end with output of generate_signals."""
    prices = pd.Series(range(1, 260), dtype=float)
    volume = pd.Series([1_000_000] * 259, dtype=float)
    sigs = generate_signals(prices, volume)
    result = momentum_score(sigs, prices)
    assert isinstance(result, MomentumScore)
    assert 0 <= result.score <= 100


# ── Bollinger Bands ────────────────────────────────────────────────────


@pytest.mark.unit
def test_bollinger_bands_flat_prices_collapse(flat_prices):
    bb = bollinger(flat_prices)
    assert bb.middle.iloc[-1] == pytest.approx(100.0)
    assert bb.upper.iloc[-1] == pytest.approx(100.0)
    assert bb.lower.iloc[-1] == pytest.approx(100.0)


@pytest.mark.unit
def test_bollinger_bands_symmetric_around_middle():
    rng = np.random.default_rng(3)
    prices = pd.Series(100 + np.cumsum(rng.normal(0, 1, 60)))
    bb = bollinger(prices)
    spread_up = bb.upper.iloc[-1] - bb.middle.iloc[-1]
    spread_down = bb.middle.iloc[-1] - bb.lower.iloc[-1]
    assert spread_up == pytest.approx(spread_down)
    assert spread_up > 0
    # Middle band is the 20-period SMA.
    assert bb.middle.iloc[-1] == pytest.approx(sma(prices, 20).iloc[-1])


@pytest.mark.unit
def test_bollinger_invalid_period():
    with pytest.raises(ValueError):
        bollinger(pd.Series([1.0, 2.0]), period=0)


# ── generate_signals: new strategy fields ──────────────────────────────


@pytest.fixture
def long_series() -> tuple[pd.Series, pd.Series]:
    rng = np.random.default_rng(11)
    prices = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, 300))))
    volume = pd.Series(rng.integers(1_000_000, 5_000_000, 300).astype(float))
    return prices, volume


@pytest.mark.unit
def test_generate_signals_new_fields_present(long_series):
    prices, volume = long_series
    signals = generate_signals(prices, volume)
    assert 0.0 <= signals["rsi_2"] <= 100.0
    assert signals["sma_5"] == pytest.approx(float(prices.tail(5).mean()))
    assert signals["bb_lower"] < signals["bb_middle"] < signals["bb_upper"]
    # Donchian windows end at the PREVIOUS bar.
    assert signals["donchian_high_20"] == pytest.approx(
        float(prices.iloc[:-1].tail(20).max())
    )
    assert signals["donchian_low_10"] == pytest.approx(
        float(prices.iloc[:-1].tail(10).min())
    )
    # Trailing returns over 21 / 126 / 252 bars.
    for name, window in (("ret_1m", 21), ("ret_6m", 126), ("ret_12m", 252)):
        base = float(prices.iloc[-window - 1])
        expected = (float(prices.iloc[-1]) - base) / base
        assert signals[name] == pytest.approx(expected), name


@pytest.mark.unit
def test_generate_signals_new_fields_none_on_short_history(simple_prices):
    volume = pd.Series([1_000_000.0] * len(simple_prices))
    signals = generate_signals(simple_prices, volume)
    assert signals["bb_lower"] is None  # needs 20 bars
    assert signals["donchian_high_20"] is None
    assert signals["ret_1m"] is None
    assert signals["ret_6m"] is None
    assert signals["ret_12m"] is None


# ── ATR / ADX / Supertrend ─────────────────────────────────────────────


@pytest.fixture
def ohlc_series() -> tuple[pd.Series, pd.Series, pd.Series]:
    rng = np.random.default_rng(5)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, 300))))
    high = close * (1 + rng.uniform(0, 0.01, len(close)))
    low = close * (1 - rng.uniform(0, 0.01, len(close)))
    return high, low, close


@pytest.mark.unit
def test_atr_positive_and_bounded(ohlc_series):
    from src.quant.indicators import atr

    high, low, close = ohlc_series
    a = atr(high, low, close, period=14)
    last = a.dropna()
    assert (last > 0).all()
    # ATR must not exceed the largest single-bar range in the window ballpark.
    assert last.iloc[-1] < float((high - low).max()) * 3


@pytest.mark.unit
def test_atr_warmup_is_nan(ohlc_series):
    from src.quant.indicators import atr

    high, low, close = ohlc_series
    a = atr(high, low, close, period=14)
    assert a.iloc[:13].isna().all()
    assert not np.isnan(a.iloc[14])


@pytest.mark.unit
def test_atr_invalid_period(ohlc_series):
    from src.quant.indicators import atr

    high, low, close = ohlc_series
    with pytest.raises(ValueError):
        atr(high, low, close, period=0)


@pytest.mark.unit
def test_adx_components_in_range(ohlc_series):
    from src.quant.indicators import adx

    high, low, close = ohlc_series
    res = adx(high, low, close, period=14)
    a = res.adx.dropna()
    assert (a >= 0).all() and (a <= 100).all()
    assert (res.plus_di.dropna() >= 0).all()
    assert (res.minus_di.dropna() >= 0).all()


@pytest.mark.unit
def test_adx_high_in_persistent_uptrend():
    from src.quant.indicators import adx

    # A clean monotonic uptrend should produce +DI > −DI.
    close = pd.Series(np.linspace(10, 60, 120))
    high = close * 1.01
    low = close * 0.99
    res = adx(high, low, close, period=14)
    assert res.plus_di.iloc[-1] > res.minus_di.iloc[-1]


@pytest.mark.unit
def test_supertrend_direction_values(ohlc_series):
    from src.quant.indicators import supertrend

    high, low, close = ohlc_series
    d = supertrend(high, low, close).dropna()
    assert set(d.unique()) <= {1.0, -1.0}


@pytest.mark.unit
def test_supertrend_uptrend_is_positive():
    from src.quant.indicators import supertrend

    close = pd.Series(np.linspace(10, 60, 120))
    high = close * 1.01
    low = close * 0.99
    d = supertrend(high, low, close)
    assert d.iloc[-1] == 1.0


@pytest.mark.unit
def test_generate_signals_atr_fields_none_without_highlow(long_series):
    prices, volume = long_series
    signals = generate_signals(prices, volume)
    for key in ("atr_14", "adx_14", "supertrend_dir", "keltner_upper"):
        assert signals[key] is None


@pytest.mark.unit
def test_generate_signals_atr_fields_present_with_highlow(long_series):
    prices, volume = long_series
    high = prices * 1.01
    low = prices * 0.99
    signals = generate_signals(prices, volume, high=high, low=low)
    assert signals["atr_14"] > 0
    assert 0 <= signals["adx_14"] <= 100
    assert signals["supertrend_dir"] in (1.0, -1.0)
    assert signals["keltner_upper"] > signals["keltner_lower"]
    assert signals["atr_pct"] == pytest.approx(signals["atr_14"] / signals["price"])
