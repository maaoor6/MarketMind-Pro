"""Unit tests for the shadow / paper-mode pipeline (Phase 2B.4)."""

from datetime import timedelta

import pytest
from src.trading.shadow import (
    ShadowBook,
    shadow_strategies,
    shadow_strategy_names,
)
from src.trading.strategies import DualMomentum, StrategyContext
from src.utils.timezone_utils import now_utc


class _FakeCache:
    """Minimal in-memory async cache stand-in."""

    def __init__(self) -> None:
        self.store: dict = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ttl=None):
        self.store[key] = value


def _ctx_buy():
    # DualMomentum BUY setup: positive 12m + 3m, price above SMA200.
    return StrategyContext(
        ticker="AAPL",
        signals={
            "price": 200.0,
            "ret_12m": 0.3,
            "ret_3m": 0.1,
            "moving_averages": {"SMA_200": 180.0},
        },
        fibonacci=None,
        weekly={},
        monthly={},
        momentum=None,
        position=None,
    )


# ── Config selectors ─────────────────────────────────────────────────────────


def test_shadow_names_empty_by_default(monkeypatch):
    monkeypatch.setattr(
        "src.trading.shadow.settings.shadow_strategy_names", "", raising=False
    )
    assert shadow_strategy_names() == set()
    assert shadow_strategies() == []


def test_shadow_selects_named(monkeypatch):
    monkeypatch.setattr(
        "src.trading.shadow.settings.shadow_strategy_names",
        "dual_momentum",
        raising=False,
    )
    names = {s.name for s in shadow_strategies()}
    assert names == {"dual_momentum"}


# ── Recording + readiness ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_observe_records_signal(monkeypatch):
    fake = _FakeCache()
    monkeypatch.setattr("src.trading.shadow.cache", fake)
    book = ShadowBook([DualMomentum()])
    await book.observe({"AAPL": _ctx_buy()})
    state = fake.store["trading:shadow:dual_momentum"]
    assert state["signal_count"] == 1
    assert state["signals"][0]["ticker"] == "AAPL"
    assert state["entered_at"]


@pytest.mark.asyncio
async def test_entered_at_set_once(monkeypatch):
    fake = _FakeCache()
    monkeypatch.setattr("src.trading.shadow.cache", fake)
    book = ShadowBook([DualMomentum()])
    await book.observe({"AAPL": _ctx_buy()})
    first = fake.store["trading:shadow:dual_momentum"]["entered_at"]
    await book.observe({"AAPL": _ctx_buy()})
    state = fake.store["trading:shadow:dual_momentum"]
    assert state["entered_at"] == first  # unchanged
    assert state["signal_count"] == 2


@pytest.mark.asyncio
async def test_promotion_not_ready_before_window(monkeypatch):
    fake = _FakeCache()
    monkeypatch.setattr("src.trading.shadow.cache", fake)
    fake.store["trading:shadow:dual_momentum"] = {
        "entered_at": now_utc().isoformat(),
        "signal_count": 5,
        "signals": [],
    }
    book = ShadowBook([DualMomentum()])
    assert not await book.promotion_ready("dual_momentum")


@pytest.mark.asyncio
async def test_promotion_ready_after_window(monkeypatch):
    fake = _FakeCache()
    monkeypatch.setattr("src.trading.shadow.cache", fake)
    old = (now_utc() - timedelta(days=31)).isoformat()
    fake.store["trading:shadow:dual_momentum"] = {
        "entered_at": old,
        "signal_count": 20,
        "signals": [],
    }
    book = ShadowBook([DualMomentum()])
    assert await book.promotion_ready("dual_momentum")
    days = await book.days_in_shadow("dual_momentum")
    assert days is not None and days >= 30


@pytest.mark.asyncio
async def test_status_reports(monkeypatch):
    fake = _FakeCache()
    monkeypatch.setattr("src.trading.shadow.cache", fake)
    book = ShadowBook([DualMomentum()])
    await book.observe({"AAPL": _ctx_buy()})
    status = await book.status()
    assert status[0].strategy == "dual_momentum"
    assert status[0].signal_count == 1
    assert status[0].ready is False
