"""Unit tests for the per-agent circuit breaker + manual force-off override."""

from unittest.mock import AsyncMock

import pytest
from src.trading.orchestrator import Orchestrator
from src.trading.stockarena_client import Portfolio, Position


class _FakeCache:
    def __init__(self) -> None:
        self.store: dict = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ttl=None):
        self.store[key] = value

    async def delete(self, key):
        self.store.pop(key, None)


def _portfolio(current_price: float) -> Portfolio:
    pos = Position("AAPL", quantity=10, avg_price=100.0, current_price=current_price)
    return Portfolio(
        cash=0.0,
        total_value=10 * current_price,
        return_pct=0.0,
        positions={"AAPL": pos},
    )


@pytest.fixture
def orch(monkeypatch):
    fake = _FakeCache()
    monkeypatch.setattr("src.trading.orchestrator.cache", fake)
    o = Orchestrator(quant=None)
    # AAPL was opened by ts_momentum → the Core Momentum agent.
    o._risk.get_entry_strategy = AsyncMock(return_value="ts_momentum")
    return o, fake


@pytest.mark.unit
@pytest.mark.asyncio
async def test_agent_breaker_freezes_on_drawdown(orch):
    o, _ = orch
    # First observation sets the high-water mark at $1000.
    await o._update_agent_breakers(_portfolio(100.0))
    assert "core_momentum" not in await o._frozen_agents()
    # Value falls to $700 — a 30% drawdown > 20% limit → freeze Core Momentum.
    await o._update_agent_breakers(_portfolio(70.0))
    frozen = await o._frozen_agents()
    assert "core_momentum" in frozen
    # Other agents are unaffected.
    assert "defensive" not in frozen


@pytest.mark.unit
@pytest.mark.asyncio
async def test_agent_breaker_no_freeze_on_small_dip(orch):
    o, _ = orch
    await o._update_agent_breakers(_portfolio(100.0))
    await o._update_agent_breakers(_portfolio(95.0))  # −5%, within limit
    assert "core_momentum" not in await o._frozen_agents()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_force_macro_off_overrides_decision(orch):
    o, fake = orch
    fake.store["trading:macro:force_off"] = "on"
    state = await o._macro_state({})
    assert state.decision == "OFF"
    assert state.size_mult == 0.0
    assert "force-off" in state.rationale
