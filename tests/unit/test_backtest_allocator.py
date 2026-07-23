"""InMemoryAllocator horizons, scoring window, and weight integration."""

import pytest
from src.backtest.allocator_sim import InMemoryAllocator
from src.trading.strategies import Action, StrategySignal

_NAMES = ["momentum_daily", "mean_reversion", "trend_following", "breakout"]
_HORIZONS = {
    "momentum_daily": 24,
    "mean_reversion": 24,
    "trend_following": 168,
    "breakout": 480,
}


def _sig(
    strategy: str, action: Action = Action.BUY, price: float = 100.0
) -> StrategySignal:
    return StrategySignal(
        strategy=strategy,
        ticker="TEST",
        action=action,
        confidence=0.8,
        reason="test",
        price=price,
    )


@pytest.fixture
def alloc() -> InMemoryAllocator:
    return InMemoryAllocator(_NAMES, _HORIZONS)


@pytest.mark.unit
def test_signal_matures_after_horizon_bars(alloc: InMemoryAllocator) -> None:
    alloc.record(_sig("momentum_daily"), bar=10)
    alloc.record(_sig("trend_following"), bar=10)
    assert alloc.score_matured(10, lambda t: 110.0) == 0
    assert alloc.score_matured(11, lambda t: 110.0) == 1  # 24h → 1 bar
    assert alloc.score_matured(14, lambda t: 110.0) == 0
    assert alloc.score_matured(15, lambda t: 110.0) == 1  # 168h → 5 bars


@pytest.mark.unit
def test_sell_signal_scores_inverted(alloc: InMemoryAllocator) -> None:
    alloc.record(_sig("momentum_daily", action=Action.SELL, price=100.0), bar=0)
    alloc.score_matured(1, lambda t: 90.0)
    assert alloc.scored[0].virtual_return_pct == pytest.approx(10.0)


@pytest.mark.unit
def test_hold_and_zero_price_ignored(alloc: InMemoryAllocator) -> None:
    alloc.record(_sig("momentum_daily", action=Action.HOLD), bar=0)
    alloc.record(_sig("momentum_daily", price=0.0), bar=0)
    assert alloc.score_matured(5, lambda t: 100.0) == 0


@pytest.mark.unit
def test_missing_price_retries_next_bar(alloc: InMemoryAllocator) -> None:
    alloc.record(_sig("momentum_daily"), bar=0)
    assert alloc.score_matured(1, lambda t: None) == 0
    assert alloc.score_matured(2, lambda t: 105.0) == 1


@pytest.mark.unit
def test_min_scored_before_real_average(alloc: InMemoryAllocator) -> None:
    for bar in range(4):
        alloc.record(_sig("momentum_daily"), bar=bar)
        alloc.score_matured(bar + 1, lambda t: 110.0)
    assert alloc.avg_returns()["momentum_daily"] is None  # 4 < 5

    alloc.record(_sig("momentum_daily"), bar=10)
    alloc.score_matured(11, lambda t: 110.0)
    assert alloc.avg_returns()["momentum_daily"] == pytest.approx(10.0)


@pytest.mark.unit
def test_recent_window_caps_at_twenty(alloc: InMemoryAllocator) -> None:
    for bar in range(30):
        alloc.record(_sig("momentum_daily", price=100.0), bar=bar * 2)
        alloc.score_matured(bar * 2 + 1, lambda t: 90.0 if bar < 10 else 110.0)
    # First 10 losses rolled out of the window: only the last 20 (+10%) remain.
    assert alloc.avg_returns()["momentum_daily"] == pytest.approx(10.0)


@pytest.mark.unit
def test_weights_favor_the_winner(alloc: InMemoryAllocator) -> None:
    for bar in range(6):
        alloc.record(_sig("momentum_daily"), bar=bar * 2)
        alloc.record(_sig("mean_reversion"), bar=bar * 2)
        # momentum wins +10%, mean_reversion loses -10%
        alloc.score_matured(bar * 2 + 1, lambda t: 110.0 if bar % 2 == 0 else 110.0)
    # Manually override: score mean_reversion as losses instead.
    alloc._recent["mean_reversion"].clear()
    alloc._recent["mean_reversion"].extend([-10.0] * 6)
    weights = alloc.weights()
    assert weights["momentum_daily"] > weights["mean_reversion"]
    assert sum(weights.values()) == pytest.approx(1.0)


@pytest.mark.unit
def test_seed_averages_prefill(alloc: InMemoryAllocator) -> None:
    alloc.seed_averages({"breakout": 5.0})
    assert alloc.avg_returns()["breakout"] == pytest.approx(5.0)
    assert alloc.avg_returns()["momentum_daily"] is None


@pytest.mark.unit
def test_regime_conditional_averages(alloc: InMemoryAllocator) -> None:
    # momentum wins in BULL, loses in BEAR — 6 signals per regime (>= min 5).
    for i in range(6):
        alloc.record(_sig("momentum_daily", price=100.0), bar=i, regime="BULL")
    alloc.score_matured(7, lambda t: 110.0)  # +10% while BULL
    for i in range(10, 16):
        alloc.record(_sig("momentum_daily", price=100.0), bar=i, regime="BEAR")
    alloc.score_matured(17, lambda t: 90.0)  # -10% while BEAR

    bull_avg = alloc.avg_returns(regime="BULL")["momentum_daily"]
    bear_avg = alloc.avg_returns(regime="BEAR")["momentum_daily"]
    assert bull_avg == pytest.approx(10.0)
    assert bear_avg == pytest.approx(-10.0)
    # Regime-conditioned weights: disabled in BEAR, enabled in BULL.
    assert alloc.weights(regime="BEAR")["momentum_daily"] == 0.0
    assert alloc.weights(regime="BULL")["momentum_daily"] > 0.0


@pytest.mark.unit
def test_regime_falls_back_to_global_below_minimum(alloc: InMemoryAllocator) -> None:
    for i in range(6):
        alloc.record(_sig("momentum_daily", price=100.0), bar=i, regime="BULL")
    alloc.score_matured(7, lambda t: 110.0)
    # Only 1 VOLATILE signal (< min 5) → falls back to the global average.
    alloc.record(_sig("momentum_daily", price=100.0), bar=20, regime="VOLATILE")
    alloc.score_matured(21, lambda t: 90.0)
    vol_avg = alloc.avg_returns(regime="VOLATILE")["momentum_daily"]
    global_avg = alloc.avg_returns()["momentum_daily"]
    assert vol_avg == pytest.approx(global_avg)
