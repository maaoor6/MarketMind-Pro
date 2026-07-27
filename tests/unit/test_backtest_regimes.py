"""Regime classification and per-regime attribution."""

import numpy as np
import pandas as pd
import pytest
from src.backtest.allocator_sim import ScoredSignal
from src.backtest.regimes import classify_regimes, regime_avg_returns, regime_metrics


def _spy_feats(closes: list[float], sma200: list[float]) -> pd.DataFrame:
    index = pd.bdate_range("2020-01-01", periods=len(closes))
    return pd.DataFrame({"Close": closes, "SMA_200": sma200}, index=index)


@pytest.mark.unit
def test_bull_bear_volatile_labels() -> None:
    feats = _spy_feats([110, 90, 110, 90], [100, 100, 100, 100])
    vix = pd.Series([15, 15, 35, 35], index=feats.index)
    labels = classify_regimes(feats, vix)
    assert list(labels) == ["BULL", "BEAR", "VOLATILE", "VOLATILE"]


@pytest.mark.unit
def test_sma_warmup_defaults_to_bull() -> None:
    feats = _spy_feats([90, 90], [np.nan, 100])
    labels = classify_regimes(feats, None)
    assert list(labels) == ["BULL", "BEAR"]


@pytest.mark.unit
def test_regime_metrics_split() -> None:
    feats = _spy_feats([110, 110, 90, 90], [100] * 4)
    labels = classify_regimes(feats, None)
    equity = pd.Series([100.0, 110.0, 99.0, 99.0], index=feats.index)
    stats = regime_metrics(equity, labels)
    assert stats["BULL"]["days"] == 1
    assert stats["BULL"]["total_return_pct"] == pytest.approx(10.0)
    assert stats["BEAR"]["days"] == 2
    assert stats["BEAR"]["total_return_pct"] == pytest.approx(-10.0)
    assert stats["VOLATILE"]["days"] == 0


@pytest.mark.unit
def test_regime_avg_returns_buckets() -> None:
    scored = [
        ScoredSignal("momentum_daily", "A", 4.0, 1, regime="BULL"),
        ScoredSignal("momentum_daily", "B", 2.0, 2, regime="BULL"),
        ScoredSignal("momentum_daily", "C", -5.0, 3, regime="BEAR"),
        ScoredSignal("breakout", "D", 1.0, 4, regime=None),
    ]
    out = regime_avg_returns(scored, ["momentum_daily", "breakout"])
    assert out["BULL"]["momentum_daily"] == pytest.approx(3.0)
    assert out["BEAR"]["momentum_daily"] == pytest.approx(-5.0)
    assert "breakout" not in out.get("BULL", {})
