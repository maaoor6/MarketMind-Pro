"""Run history store: append/load, weighted aggregation, rotation."""

import pytest
from src.backtest.history import (
    aggregate,
    append_run,
    load_runs,
    pick_rotation,
    ticker_coverage,
)


@pytest.mark.unit
def test_append_and_load_roundtrip(tmp_path) -> None:
    path = tmp_path / "history.json"
    assert load_runs(path) == []
    append_run({"mode": "all", "tickers": ["SPY"]}, path)
    append_run({"mode": "agent", "tickers": ["AAPL"]}, path)
    runs = load_runs(path)
    assert len(runs) == 2
    assert runs[0]["mode"] == "all"
    assert "generated_at" in runs[0]


@pytest.mark.unit
def test_load_corrupt_file_fails_open(tmp_path) -> None:
    path = tmp_path / "history.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_runs(path) == []


@pytest.mark.unit
def test_aggregate_weights_by_scored_count() -> None:
    runs = [
        {"strategy_stats": {"momentum_daily": {"avg_return_pct": 10.0, "scored": 1}}},
        {"strategy_stats": {"momentum_daily": {"avg_return_pct": 1.0, "scored": 9}}},
    ]
    agg = aggregate(runs)
    # (10*1 + 1*9) / 10 = 1.9 — the bigger sample dominates.
    assert agg["avg_returns"]["momentum_daily"] == pytest.approx(1.9)
    assert agg["runs"] == 2


@pytest.mark.unit
def test_aggregate_regime_stats() -> None:
    runs = [
        {
            "strategy_stats": {},
            "regime_stats": {
                "BULL": {"breakout": {"avg_return_pct": 2.0, "scored": 4}},
                "BEAR": {"breakout": {"avg_return_pct": -3.0, "scored": 2}},
            },
        }
    ]
    agg = aggregate(runs)
    assert agg["regime_avg_returns"]["BULL"]["breakout"] == pytest.approx(2.0)
    assert agg["regime_avg_returns"]["BEAR"]["breakout"] == pytest.approx(-3.0)


@pytest.mark.unit
def test_pick_rotation_prefers_least_tested() -> None:
    runs = [{"tickers": ["AAA", "BBB"]}, {"tickers": ["AAA"]}]
    assert ticker_coverage(runs) == {"AAA": 2, "BBB": 1}
    picked = pick_rotation(["AAA", "BBB", "CCC", "DDD"], 3, runs)
    assert picked[0] == "SPY"
    # The two never-tested tickers must be chosen before AAA/BBB.
    assert set(picked[1:]) == {"CCC", "DDD"}


@pytest.mark.unit
def test_pick_rotation_deterministic_per_history_length() -> None:
    runs: list[dict] = []
    first = pick_rotation(["A", "B", "C", "D", "E"], 3, runs)
    second = pick_rotation(["A", "B", "C", "D", "E"], 3, runs)
    assert first == second
