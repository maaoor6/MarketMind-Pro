"""Unit tests for the execution drift tracker."""

import pytest
from src.trading.execution_tracker import ExecutionTracker, adverse_drift_pct


class _FakeCache:
    def __init__(self) -> None:
        self.store: dict = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ttl=None):
        self.store[key] = value


@pytest.mark.unit
def test_adverse_drift_buy_pays_more_is_positive():
    assert adverse_drift_pct(100, 101, "buy") == pytest.approx(1.0)


@pytest.mark.unit
def test_adverse_drift_sell_lower_is_positive():
    assert adverse_drift_pct(100, 99, "sell") == pytest.approx(1.0)


@pytest.mark.unit
def test_adverse_drift_favorable_is_negative():
    assert adverse_drift_pct(100, 99, "buy") == pytest.approx(-1.0)


@pytest.mark.unit
def test_adverse_drift_invalid_prices():
    assert adverse_drift_pct(0, 100, "buy") is None
    assert adverse_drift_pct(100, 0, "sell") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tracker_accumulates_and_summarizes(monkeypatch):
    fake = _FakeCache()
    monkeypatch.setattr("src.trading.execution_tracker.cache", fake)
    tracker = ExecutionTracker()
    await tracker.record(100, 101, "buy")  # +1%
    await tracker.record(100, 100.5, "buy")  # +0.5%
    summary = await ExecutionTracker.summary()
    assert summary.count == 2
    assert summary.avg_adverse_pct == pytest.approx(0.75)
    assert summary.worst_adverse_pct == pytest.approx(1.0)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tracker_summary_empty_is_none(monkeypatch):
    monkeypatch.setattr("src.trading.execution_tracker.cache", _FakeCache())
    assert await ExecutionTracker.summary() is None
