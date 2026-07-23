"""Equivalence tests: precomputed features must match the live code path.

This is the correctness gate for the whole backtest — build_context at bar t
must produce the same StrategyContext the live agent would build from the
slice ending at t.
"""

import numpy as np
import pandas as pd
import pytest
from src.backtest.features import (
    BURN_IN_BARS,
    build_context,
    fib_snapshot,
    precompute_features,
)
from src.quant.fibonacci import calculate_fibonacci
from src.quant.indicators import generate_signals, momentum_score

_BARS = 400
_CHECK_BAR = 350


@pytest.fixture(scope="module")
def synthetic_df() -> pd.DataFrame:
    """Deterministic random-walk OHLCV frame."""
    rng = np.random.default_rng(7)
    index = pd.bdate_range("2020-01-01", periods=_BARS)
    closes = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.015, _BARS)))
    volume = rng.integers(1_000_000, 20_000_000, _BARS).astype(float)
    # Force a couple of volume spikes so that path is exercised.
    volume[300] = volume[290:300].mean() * 5
    return pd.DataFrame(
        {
            "Open": closes * (1 + rng.normal(0, 0.002, _BARS)),
            "High": closes * 1.01,
            "Low": closes * 0.99,
            "Close": closes,
            "Volume": volume,
        },
        index=index,
    )


@pytest.mark.unit
def test_signals_match_generate_signals(synthetic_df: pd.DataFrame) -> None:
    feats = precompute_features(synthetic_df)
    for bar in (_CHECK_BAR, BURN_IN_BARS, _BARS - 1):
        ctx = build_context("TEST", feats, bar)
        assert ctx is not None
        sliced = synthetic_df.iloc[: bar + 1]
        expected = generate_signals(
            sliced["Close"],
            sliced["Volume"],
            high=sliced["High"],
            low=sliced["Low"],
        )

        got = ctx.signals
        assert got["price"] == pytest.approx(expected["price"])
        assert got["rsi"] == pytest.approx(expected["rsi"], rel=1e-9)
        assert got["rsi_signal"] == expected["rsi_signal"]
        assert got["macd_line"] == pytest.approx(expected["macd_line"], rel=1e-9)
        assert got["macd_signal"] == pytest.approx(expected["macd_signal"], rel=1e-9)
        assert got["macd_histogram"] == pytest.approx(
            expected["macd_histogram"], rel=1e-9
        )
        assert got["macd_histogram_prev"] == pytest.approx(
            expected["macd_histogram_prev"], rel=1e-9
        )
        assert got["volume_spike"] == expected["volume_spike"]
        for key in (
            "rsi_2",
            "sma_5",
            "bb_upper",
            "bb_middle",
            "bb_lower",
            "donchian_high_20",
            "donchian_low_10",
            "ret_1m",
            "ret_6m",
            "ret_12m",
            "low_7d_close",
            "high_7d_close",
            "sma_50_prev",
            "sma_200_prev",
            "vol_20d",
            "atr_14",
            "atr_pct",
            "adx_14",
            "plus_di",
            "minus_di",
            "supertrend_dir",
            "keltner_upper",
            "keltner_lower",
        ):
            if expected[key] is None:
                assert got[key] is None, key
            else:
                assert got[key] == pytest.approx(expected[key], rel=1e-9), key
        for key, value in expected["moving_averages"].items():
            if np.isnan(value):
                assert key not in got["moving_averages"]
            else:
                assert got["moving_averages"][key] == pytest.approx(
                    value, rel=1e-9
                ), key


@pytest.mark.unit
def test_fibonacci_matches_calculate_fibonacci(synthetic_df: pd.DataFrame) -> None:
    """Fib window ends at the PREVIOUS bar — live semantics: a fresh quote is
    compared against levels computed from already-closed daily bars."""
    feats = precompute_features(synthetic_df)
    for bar in (_CHECK_BAR, BURN_IN_BARS, _BARS - 1):
        ctx = build_context("TEST", feats, bar)
        assert ctx is not None
        prior_closes = synthetic_df["Close"].iloc[:bar]  # up to bar-1 inclusive
        expected = calculate_fibonacci(prior_closes, ticker="TEST")

        fib = ctx.fibonacci
        assert fib is not None
        assert fib["high_52w"] == pytest.approx(expected.high_52w)
        assert fib["low_52w"] == pytest.approx(expected.low_52w)
        # Trend still uses today's price vs 20 bars back (live parity).
        full = synthetic_df["Close"].iloc[: bar + 1]
        assert fib["trend"] == calculate_fibonacci(full, ticker="TEST").trend
        # Support/resistance: same level-selection math vs today's price.
        price = float(full.iloc[-1])
        retracements = list(expected.retracements.values())
        below = [v for v in retracements if v < price]
        above = [v for v in retracements if v > price]
        assert fib["nearest_support"] == pytest.approx(
            max(below) if below else expected.low_52w
        )
        assert fib["nearest_resistance"] == pytest.approx(
            min(above) if above else expected.high_52w
        )


@pytest.mark.unit
def test_momentum_matches_live_computation(synthetic_df: pd.DataFrame) -> None:
    feats = precompute_features(synthetic_df)
    ctx = build_context("TEST", feats, _CHECK_BAR)
    assert ctx is not None
    sliced = synthetic_df.iloc[: _CHECK_BAR + 1]
    expected = momentum_score(
        generate_signals(sliced["Close"], sliced["Volume"]), sliced["Close"]
    )
    assert ctx.momentum is not None
    assert ctx.momentum.score == expected.score
    assert ctx.momentum.breakdown == expected.breakdown


@pytest.mark.unit
def test_burn_in_and_bounds() -> None:
    rng = np.random.default_rng(1)
    index = pd.bdate_range("2020-01-01", periods=300)
    closes = 100 + np.cumsum(rng.normal(0, 1, 300))
    df = pd.DataFrame(
        {
            "Open": closes,
            "High": closes,
            "Low": closes,
            "Close": closes,
            "Volume": np.full(300, 1e6),
        },
        index=index,
    )
    feats = precompute_features(df)
    assert build_context("TEST", feats, BURN_IN_BARS - 1) is None
    assert build_context("TEST", feats, 299) is not None
    assert build_context("TEST", feats, 300) is None


@pytest.mark.unit
def test_fib_snapshot_flat_prices_returns_none() -> None:
    assert fib_snapshot(100.0, 100.0, 100.0, "UPTREND") is None


@pytest.mark.unit
def test_fib_snapshot_above_high_falls_back_to_high() -> None:
    snap = fib_snapshot(110.0, 90.0, 111.0, "UPTREND")
    assert snap is not None
    assert snap["nearest_resistance"] == pytest.approx(110.0)
    assert snap["nearest_support"] == pytest.approx(110.0)  # 0% retracement < price
