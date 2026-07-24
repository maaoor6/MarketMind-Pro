"""Unit tests for manual trading ops (flatten/close) + orchestrator execution."""

from unittest.mock import AsyncMock

import pytest
from src.trading import manual_ops
from src.trading.stockarena_client import Position


class _FakeCache:
    def __init__(self) -> None:
        self.store: dict = {}

    async def get(self, k):
        return self.store.get(k)

    async def set(self, k, v, ttl=None):
        self.store[k] = v

    async def delete(self, k):
        self.store.pop(k, None)


@pytest.fixture
def fake_cache(monkeypatch):
    fake = _FakeCache()
    monkeypatch.setattr("src.trading.manual_ops.cache", fake)
    return fake


# ── Protocol ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_request_and_pop_flatten(fake_cache):
    await manual_ops.request_flatten()
    ops = await manual_ops.pop_pending()
    assert ops.flatten
    assert ops.any()
    # Draining clears it.
    ops2 = await manual_ops.pop_pending()
    assert not ops2.any()


@pytest.mark.asyncio
async def test_request_close_dedup(fake_cache):
    await manual_ops.request_close("aapl")
    await manual_ops.request_close("AAPL")
    await manual_ops.request_close("MSFT")
    ops = await manual_ops.pop_pending()
    assert ops.close == ["AAPL", "MSFT"]


@pytest.mark.asyncio
async def test_ack_roundtrip(fake_cache):
    await manual_ops.write_ack("manual flatten: sold ['AAPL']")
    ack = await manual_ops.read_ack()
    assert ack and "AAPL" in ack["summary"]


# ── Orchestrator execution ───────────────────────────────────────────────────


class _Portfolio:
    def __init__(self, positions):
        self.positions = positions
        self.total_value = 10000.0


@pytest.mark.asyncio
async def test_orchestrator_flatten_sells_all(monkeypatch):
    from src.trading.orchestrator import Orchestrator

    monkeypatch.setattr("src.trading.orchestrator.pop_pending", AsyncMock())
    orch = Orchestrator(quant=None)

    pos = {
        "AAPL": Position("AAPL", 10, 100.0, 110.0),
        "MSFT": Position("MSFT", 5, 200.0, 210.0),
    }
    portfolio = _Portfolio(pos)

    # Pretend a flatten is queued.
    from src.trading.manual_ops import ManualOps

    monkeypatch.setattr(
        "src.trading.orchestrator.pop_pending",
        AsyncMock(return_value=ManualOps(flatten=True)),
    )
    monkeypatch.setattr("src.trading.orchestrator.write_ack", AsyncMock())
    orch._notifier.push = AsyncMock()

    executed = []

    async def _fake_execute(plan, pf, extended):
        executed.append(plan.ticker)
        return pf

    orch._execute = _fake_execute
    await orch._run_manual_ops(portfolio, extended=False)
    assert set(executed) == {"AAPL", "MSFT"}
    orch._notifier.push.assert_awaited()


@pytest.mark.asyncio
async def test_orchestrator_no_ops_noop(monkeypatch):
    from src.trading.manual_ops import ManualOps
    from src.trading.orchestrator import Orchestrator

    monkeypatch.setattr(
        "src.trading.orchestrator.pop_pending",
        AsyncMock(return_value=ManualOps()),
    )
    orch = Orchestrator(quant=None)
    orch._execute = AsyncMock()
    portfolio = _Portfolio({"AAPL": Position("AAPL", 10, 100.0, 110.0)})
    result = await orch._run_manual_ops(portfolio, extended=False)
    assert result is portfolio
    orch._execute.assert_not_called()
