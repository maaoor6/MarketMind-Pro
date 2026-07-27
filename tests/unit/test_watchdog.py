"""Unit tests for the infrastructure watchdog (Phase 5.3a)."""

from unittest.mock import AsyncMock

import pytest
from src.utils.watchdog import DEGRADED_KEY, Watchdog, evaluate

# ── Pure threshold evaluation ────────────────────────────────────────────────


def test_healthy_within_thresholds():
    r = evaluate(mem_pct=50.0, cpu_pct=40.0, redis_ok=True, mem_max=90.0, cpu_max=95.0)
    assert r.healthy
    assert r.alerts == []


def test_memory_breach():
    r = evaluate(mem_pct=95.0, cpu_pct=10.0, redis_ok=True, mem_max=90.0, cpu_max=95.0)
    assert not r.healthy
    assert any("memory" in a for a in r.alerts)


def test_cpu_breach():
    r = evaluate(mem_pct=10.0, cpu_pct=99.0, redis_ok=True, mem_max=90.0, cpu_max=95.0)
    assert not r.healthy
    assert any("cpu" in a for a in r.alerts)


def test_redis_down_breach():
    r = evaluate(mem_pct=10.0, cpu_pct=10.0, redis_ok=False, mem_max=90.0, cpu_max=95.0)
    assert not r.healthy
    assert "redis unreachable" in r.alerts


def test_missing_metrics_fail_open():
    # None metrics must not trip the watchdog.
    r = evaluate(mem_pct=None, cpu_pct=None, redis_ok=True, mem_max=90.0, cpu_max=95.0)
    assert r.healthy


# ── check_once behaviour ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_once_alerts_and_flags(monkeypatch):
    store: dict = {}

    class _Cache:
        async def get(self, k):
            return store.get(k)

        async def set(self, k, v, ttl=None):
            store[k] = v

        async def delete(self, k):
            store.pop(k, None)

    monkeypatch.setattr("src.utils.watchdog.cache", _Cache())
    monkeypatch.setattr("src.utils.watchdog.read_mem_pct", lambda: 99.0)
    monkeypatch.setattr("src.utils.watchdog.read_cpu_pct", lambda: 10.0)

    notifier = AsyncMock()
    wd = Watchdog(notifier=notifier)
    report = await wd.check_once()
    assert not report.healthy
    assert DEGRADED_KEY in store  # degraded flag raised
    notifier.push.assert_awaited()


@pytest.mark.asyncio
async def test_check_once_clears_flag_when_healthy(monkeypatch):
    store: dict = {DEGRADED_KEY: {"alerts": ["old"]}}

    class _Cache:
        async def get(self, k):
            return store.get(k)

        async def set(self, k, v, ttl=None):
            store[k] = v

        async def delete(self, k):
            store.pop(k, None)

    monkeypatch.setattr("src.utils.watchdog.cache", _Cache())
    monkeypatch.setattr("src.utils.watchdog.read_mem_pct", lambda: 20.0)
    monkeypatch.setattr("src.utils.watchdog.read_cpu_pct", lambda: 20.0)

    wd = Watchdog(notifier=None)
    report = await wd.check_once()
    assert report.healthy
    assert DEGRADED_KEY not in store  # cleared on recovery
