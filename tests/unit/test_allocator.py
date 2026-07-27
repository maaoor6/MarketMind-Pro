"""Unit tests for the adaptive strategy allocator's weight math."""

import pytest
from src.trading.allocator import compute_weights

NAMES = ["momentum_daily", "mean_reversion", "trend_following", "breakout"]


@pytest.mark.unit
def test_weights_normalized_to_one():
    weights = compute_weights(
        {
            "momentum_daily": 5.0,
            "mean_reversion": -2.0,
            "trend_following": 0.0,
            "breakout": 1.0,
        },
        NAMES,
    )
    assert sum(weights.values()) == pytest.approx(1.0)
    assert set(weights) == set(NAMES)


@pytest.mark.unit
def test_winner_gets_more_weight_than_loser():
    weights = compute_weights(
        {
            "momentum_daily": 8.0,
            "mean_reversion": -8.0,
            "trend_following": 0.0,
            "breakout": 0.0,
        },
        NAMES,
    )
    assert weights["momentum_daily"] > weights["breakout"]
    assert weights["breakout"] > weights["mean_reversion"]


@pytest.mark.unit
def test_negative_average_disables_strategy():
    weights = compute_weights(
        {
            "momentum_daily": 0.0,
            "mean_reversion": -99.0,
            "trend_following": 0.0,
            "breakout": 0.0,
        },
        NAMES,
    )
    # Proven loser gets weight 0 — its signals never trade. It keeps being
    # recorded/scored, so it re-enables the moment its average recovers.
    assert weights["mean_reversion"] == 0.0
    assert sum(weights.values()) == pytest.approx(1.0)


@pytest.mark.unit
def test_disabled_strategy_reenables_on_recovery():
    before = compute_weights({"momentum_daily": 1.0, "mean_reversion": -0.5}, NAMES[:2])
    after = compute_weights({"momentum_daily": 1.0, "mean_reversion": 0.2}, NAMES[:2])
    assert before["mean_reversion"] == 0.0
    assert after["mean_reversion"] > 0.0


@pytest.mark.unit
def test_sensitivity_amplifies_small_averages():
    # Per-signal averages are fractions of a percent; the sensitivity factor
    # must turn that into a REAL weight gap (this was the uniform-weights bug).
    weights = compute_weights(
        {"momentum_daily": 1.28, "breakout": 0.02}, ["momentum_daily", "breakout"]
    )
    assert weights["momentum_daily"] > weights["breakout"] * 1.5


@pytest.mark.unit
def test_extreme_average_capped():
    weights = compute_weights(
        {"momentum_daily": 500.0, "breakout": 4.0}, ["momentum_daily", "breakout"]
    )
    # Cap at 3.0 raw: 500% avg can't dominate a healthy 4% strategy 100:1.
    assert weights["momentum_daily"] / weights["breakout"] <= 1.0 + 1e-9


@pytest.mark.unit
def test_cold_start_all_unknown_gives_equal_weights():
    weights = compute_weights(dict.fromkeys(NAMES), NAMES)
    for weight in weights.values():
        assert weight == pytest.approx(0.25)


@pytest.mark.unit
def test_cold_start_strategy_gets_mean_weight():
    weights = compute_weights(
        {
            "momentum_daily": 2.0,
            "mean_reversion": -4.0,
            "trend_following": None,
            "breakout": 0.5,
        },
        NAMES,
    )
    # Unknown strategy gets the mean of the ENABLED scored raws — above the
    # disabled loser, between the weak and the strong performer.
    assert weights["mean_reversion"] < weights["trend_following"]
    assert weights["breakout"] < weights["trend_following"] < weights["momentum_daily"]


@pytest.mark.unit
def test_load_weight_seeds_missing_file_fails_open(tmp_path):
    from src.trading.allocator import load_weight_seeds

    assert load_weight_seeds(tmp_path / "nope.json") == {}


@pytest.mark.unit
def test_load_weight_seeds_corrupt_file_fails_open(tmp_path):
    from src.trading.allocator import load_weight_seeds

    path = tmp_path / "weights.json"
    path.write_text("{broken", encoding="utf-8")
    assert load_weight_seeds(path) == {}


@pytest.mark.unit
def test_load_weight_seeds_clamps_extremes(tmp_path):
    import json

    from src.trading.allocator import load_weight_seeds

    path = tmp_path / "weights.json"
    path.write_text(
        json.dumps(
            {
                "avg_returns": {"momentum_daily": 500.0, "breakout": -3.2},
                "regime_avg_returns": {
                    "BEAR": {"mean_reversion": -999.0, "bad": "not-a-number"}
                },
            }
        ),
        encoding="utf-8",
    )
    seeds = load_weight_seeds(path)
    # Safety clamp: a corrupt/extreme seed can't distort live sizing.
    assert seeds["avg_returns"]["momentum_daily"] == pytest.approx(20.0)
    assert seeds["avg_returns"]["breakout"] == pytest.approx(-3.2)
    assert seeds["regime_avg_returns"]["BEAR"]["mean_reversion"] == pytest.approx(-20.0)
    assert "bad" not in seeds["regime_avg_returns"]["BEAR"]


@pytest.mark.unit
def test_seeded_cold_start_beats_flat_mean():
    # Simulates what get_weights builds when the DB is empty but seeds exist:
    # seeded averages flow into compute_weights instead of None.
    weights = compute_weights(
        {
            "momentum_daily": 6.0,  # from seed file
            "mean_reversion": -6.0,  # from seed file
            "trend_following": None,  # no seed, no live data
            "breakout": 2.0,
        },
        NAMES,
    )
    assert weights["momentum_daily"] > weights["breakout"]
    assert weights["breakout"] > weights["mean_reversion"]
    assert sum(weights.values()) == pytest.approx(1.0)
