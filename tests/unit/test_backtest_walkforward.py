"""Walk-forward windowing: no train/test overlap, continuous OOS curve."""

import numpy as np
import pandas as pd
import pytest
from src.backtest.features import BURN_IN_BARS, precompute_features
from src.backtest.walkforward import _slice_with_leadin, run_walk_forward


def _make_features(bars: int, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2010-01-01", periods=bars)
    closes = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, bars)))
    df = pd.DataFrame(
        {
            "Open": closes * (1 + rng.normal(0, 0.001, bars)),
            "High": closes * 1.01,
            "Low": closes * 0.99,
            "Close": closes,
            "Volume": rng.integers(2_000_000, 30_000_000, bars).astype(float),
        },
        index=index,
    )
    return precompute_features(df)


@pytest.mark.unit
def test_slice_with_leadin_boundaries() -> None:
    feats = _make_features(1200)
    start, end = feats.index[500], feats.index[800]
    sliced = _slice_with_leadin(feats, start, end)
    assert sliced is not None
    assert sliced.index[BURN_IN_BARS] == start  # trading begins exactly at start
    assert sliced.index[-1] == end
    assert sliced.index[0] == feats.index[500 - BURN_IN_BARS]


@pytest.mark.unit
def test_slice_too_short_returns_none() -> None:
    feats = _make_features(300)
    assert _slice_with_leadin(feats, feats.index[280], feats.index[290]) is None


@pytest.mark.unit
def test_walkforward_windows_do_not_overlap_and_curve_is_continuous() -> None:
    # ~8 years of data → 3y train + 1y test rolls several windows.
    features = {"SPY": _make_features(2000, seed=5)}
    result = run_walk_forward(
        features, train_years=3, test_years=1, dynamic_universe=False
    )
    assert len(result.windows) >= 2
    for window in result.windows:
        assert window.train_end == window.test_start  # no gap, no overlap
        assert window.train_start < window.train_end < window.test_end
    # Consecutive windows: next train ends where previous test ended (rolling).
    for prev, cur in zip(result.windows, result.windows[1:]):
        assert cur.train_start == prev.train_start + pd.DateOffset(years=1)

    equity = result.oos_equity
    assert equity.index.is_monotonic_increasing
    assert not equity.index.duplicated().any()
    assert float(equity.iloc[0]) == pytest.approx(10_000.0, rel=1e-6)
    # No absurd jumps at window seams (curve chained by returns).
    assert equity.pct_change().abs().max() < 0.5


@pytest.mark.unit
def test_walkforward_trades_only_in_test_windows() -> None:
    features = {"SPY": _make_features(2000, seed=5)}
    result = run_walk_forward(
        features, train_years=3, test_years=1, dynamic_universe=False
    )
    first_test_start = result.windows[0].test_start
    assert all(t.bar_date >= first_test_start for t in result.trades)
