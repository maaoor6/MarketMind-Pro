"""Unit tests for the data-quality gate (validate_history)."""

import numpy as np
import pandas as pd
import pytest
from src.backtest.validate import summarize_reports, validate_history


def _frame(closes: list[float], start: str = "1990-01-01") -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=len(closes))
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame(
        {"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1e6}
    )


@pytest.mark.unit
def test_clean_series_is_untouched():
    rng = np.random.default_rng(1)
    closes = list(100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, 400))))
    df = _frame(closes)
    clean, rep = validate_history(df, "CLEAN")
    assert rep.trimmed_bars == 0
    assert len(clean) == len(df)
    assert rep.reasons == []


@pytest.mark.unit
def test_leading_flat_run_is_trimmed():
    # 120 frozen bars (> 60 limit) then a real random walk.
    rng = np.random.default_rng(2)
    tail = list(50 * np.exp(np.cumsum(rng.normal(0, 0.01, 300))))
    closes = [10.0] * 120 + tail
    df = _frame(closes)
    clean, rep = validate_history(df, "FROZEN")
    assert rep.trimmed_bars >= 120
    assert 10.0 not in list(clean["Close"].iloc[:5])
    assert any("flat_run" in r for r in rep.reasons)


@pytest.mark.unit
def test_extreme_jump_is_trimmed():
    # A 300% overnight jump in the early zone is an adjustment artifact.
    rng = np.random.default_rng(3)
    pre = list(2 + rng.normal(0, 0.02, 80))
    post = list(20 * np.exp(np.cumsum(rng.normal(0, 0.01, 300))))
    df = _frame(pre + post)
    clean, rep = validate_history(df, "JUMP")
    assert rep.trimmed_bars >= 80
    assert any("jump" in r for r in rep.reasons)


@pytest.mark.unit
def test_real_crash_is_kept():
    # A −40% crash (real, < 250% and not preceded by a flat run) is preserved.
    rng = np.random.default_rng(4)
    a = list(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 150))))
    b = [a[-1] * 0.6]  # −40% single day
    c = list(a[-1] * 0.6 * np.exp(np.cumsum(rng.normal(0, 0.01, 150))))
    df = _frame(a + b + c)
    _, rep = validate_history(df, "CRASH")
    assert rep.trimmed_bars == 0


@pytest.mark.unit
def test_short_flat_run_is_kept():
    # A 30-bar flat run (below the 60 limit) is thin trading, not corruption.
    rng = np.random.default_rng(6)
    closes = (
        list(100 + rng.normal(0, 1, 100))
        + [50.0] * 30
        + list(50 + rng.normal(0, 1, 200))
    )
    df = _frame(closes)
    _, rep = validate_history(df, "THIN")
    assert rep.trimmed_bars == 0


@pytest.mark.unit
def test_empty_frame():
    df = pd.DataFrame({"Close": []}, dtype=float)
    clean, rep = validate_history(df, "EMPTY")
    assert rep.trimmed_bars == 0
    assert rep.reasons == ["empty"]


@pytest.mark.unit
def test_summarize_reports_aggregates():
    rng = np.random.default_rng(7)
    clean = _frame(list(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300)))))
    frozen = _frame([10.0] * 120 + list(50 + rng.normal(0, 1, 300)))
    reports = [
        validate_history(clean, "A")[1],
        validate_history(frozen, "B")[1],
    ]
    summary = summarize_reports(reports)
    assert summary["tickers_checked"] == 2
    assert summary["tickers_cleaned"] == 1
    assert summary["bars_removed"] >= 120
    assert summary["worst"][0]["ticker"] == "B"
