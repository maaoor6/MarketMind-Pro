"""Unit tests for the concept-drift monitor + auto-quarantine (Phase 2C.3)."""

import pytest
from src.trading.drift_monitor import (
    DriftMonitor,
    cusum_decay,
    ks_drift,
    ks_pvalue,
    ks_statistic,
    quarantined_strategies,
)

# ── Pure statistics ──────────────────────────────────────────────────────────


def test_cusum_no_decay_when_at_target():
    # Returns hover around target → no sustained shortfall.
    values = [0.01, 0.009, 0.011, 0.01, 0.012]
    assert not cusum_decay(values, target=0.01, threshold=0.05)


def test_cusum_detects_sustained_underperformance():
    # Persistent returns well below target accumulate past the threshold.
    values = [-0.02] * 10
    assert cusum_decay(values, target=0.01, threshold=0.05)


def test_ks_statistic_identical_is_zero():
    a = [0.01, 0.02, 0.03, 0.04]
    assert ks_statistic(a, a) == 0.0


def test_ks_statistic_disjoint_is_one():
    assert ks_statistic([0.0, 0.1], [1.0, 1.1]) == pytest.approx(1.0)


def test_ks_pvalue_bounds():
    assert ks_pvalue(0.0, 10, 10) == 1.0
    assert 0.0 <= ks_pvalue(0.9, 30, 30) <= 1.0


def test_ks_drift_flags_worse_shifted_sample():
    reference = [0.02] * 30
    worse = [-0.05] * 30
    assert ks_drift(worse, reference, alpha=0.05)


def test_ks_drift_ignores_better_sample():
    reference = [0.0] * 30
    better = [0.05] * 30
    # Different distribution but BETTER — must not flag drift.
    assert not ks_drift(better, reference, alpha=0.05)


# ── Monitor ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_monitor_insufficient_samples():
    async def fetch(_):
        return [0.01, 0.02]  # below drift_min_samples

    async def ref(_):
        return 0.01

    v = await DriftMonitor(fetch, ref).check("dual_momentum")
    assert not v.drift
    assert v.reason == "insufficient samples"


@pytest.mark.asyncio
async def test_monitor_flags_cusum_decay():
    async def fetch(_):
        return [-0.03] * 30

    async def ref(_):
        return 0.01

    v = await DriftMonitor(fetch, ref).check("ts_momentum")
    assert v.drift
    assert "cusum" in v.reason


@pytest.mark.asyncio
async def test_monitor_stable_strategy():
    async def fetch(_):
        return [0.011, 0.012, 0.01] * 10  # around target

    async def ref(_):
        return 0.01

    v = await DriftMonitor(fetch, ref).check("breakout")
    assert not v.drift


@pytest.mark.asyncio
async def test_check_and_quarantine_writes_flag(monkeypatch):
    store: dict = {}

    class _Cache:
        async def get(self, k):
            return store.get(k)

        async def set(self, k, v, ttl=None):
            store[k] = v

    monkeypatch.setattr("src.trading.drift_monitor.cache", _Cache())

    async def fetch(_):
        return [-0.05] * 30

    async def ref(_):
        return 0.01

    monitor = DriftMonitor(fetch, ref)
    verdicts = await monitor.check_and_quarantine(["bad_strat"])
    assert verdicts[0].drift
    assert "trading:quarantine:bad_strat" in store
    # And it shows up in the quarantine set.
    q = await quarantined_strategies(["bad_strat", "good_strat"])
    assert q == {"bad_strat"}
