"""Unit tests for the market-data provider abstraction (src/data)."""

import pandas as pd
import pytest
from src.data.provider import (
    CAP_LIVE_PRICE,
    CAP_OHLCV,
    MarketDataProvider,
    NotSupportedError,
    ProviderResult,
)
from src.data.providers.yfinance_provider import YFinanceProvider
from src.data.registry import (
    CompositeProvider,
    get_provider,
    register_provider,
    reset_provider_cache,
)


class _FakeOK(MarketDataProvider):
    """A provider that always answers OHLCV + live price."""

    name = "fake_ok"
    capabilities = frozenset({CAP_OHLCV, CAP_LIVE_PRICE})

    def __init__(self, price: float = 100.0) -> None:
        self._price = price

    async def fetch_ohlcv(self, ticker, period="1y", interval="1d"):
        return pd.DataFrame({"Close": [self._price, self._price + 1]})

    async def fetch_live_price(self, ticker):
        return self._price, self._price - 1


class _FakeBoom(MarketDataProvider):
    """A provider that declares OHLCV but always raises (to exercise fail-over)."""

    name = "fake_boom"
    capabilities = frozenset({CAP_OHLCV})

    async def fetch_ohlcv(self, ticker, period="1y", interval="1d"):
        raise ValueError("boom no data")


# ── Base protocol ──────────────────────────────────────────────────────────


def test_base_supports_and_capabilities():
    p = YFinanceProvider()
    assert p.supports(CAP_OHLCV)
    assert p.supports(CAP_LIVE_PRICE)
    assert not p.supports("fundamentals")


@pytest.mark.asyncio
async def test_unimplemented_capability_raises_not_supported():
    p = _FakeOK()
    with pytest.raises(NotSupportedError):
        await p.fetch_fundamentals("AAPL")


# ── Composite fail-over + provenance ────────────────────────────────────────


@pytest.mark.asyncio
async def test_failover_skips_broken_provider():
    comp = CompositeProvider([_FakeBoom(), _FakeOK(price=42.0)])
    result = await comp.fetch_ohlcv_result("AAPL")
    assert isinstance(result, ProviderResult)
    assert result.source == "fake_ok"  # skipped the boom provider
    assert result.data["Close"].iloc[0] == 42.0


@pytest.mark.asyncio
async def test_plain_call_returns_raw_data():
    comp = CompositeProvider([_FakeOK(price=7.0)])
    price, prev = await comp.fetch_live_price("AAPL")
    assert price == 7.0
    assert prev == 6.0


@pytest.mark.asyncio
async def test_all_providers_fail_raises_last_exception():
    comp = CompositeProvider([_FakeBoom()])
    # The original ValueError message must propagate unchanged (QuantEngine relies on it).
    with pytest.raises(ValueError, match="boom no data"):
        await comp.fetch_ohlcv("AAPL")


@pytest.mark.asyncio
async def test_capability_with_no_provider_raises_not_supported():
    comp = CompositeProvider([_FakeOK()])  # no SCREEN capability anywhere
    with pytest.raises(NotSupportedError):
        await comp.screen("day_gainers")


def test_composite_aggregates_capabilities():
    comp = CompositeProvider([_FakeBoom(), _FakeOK()])
    assert CAP_OHLCV in comp.capabilities
    assert CAP_LIVE_PRICE in comp.capabilities


@pytest.mark.asyncio
async def test_provider_order_is_priority():
    # Both answer OHLCV; the first in the list must win.
    comp = CompositeProvider([_FakeOK(price=1.0), _FakeOK(price=2.0)])
    result = await comp.fetch_ohlcv_result("AAPL")
    assert result.data["Close"].iloc[0] == 1.0


# ── Registry ────────────────────────────────────────────────────────────────


def test_get_provider_is_cached_and_resettable():
    reset_provider_cache()
    a = get_provider()
    b = get_provider()
    assert a is b
    reset_provider_cache()
    c = get_provider()
    assert c is not a


def test_registry_custom_provider(monkeypatch):
    register_provider("fake_ok", _FakeOK)
    monkeypatch.setattr(
        "src.data.registry.settings.data_provider_priority",
        "fake_ok,yfinance",
        raising=False,
    )
    reset_provider_cache()
    comp = get_provider()
    names = [p.name for p in comp.providers]
    assert names[0] == "fake_ok"
    reset_provider_cache()


def test_unknown_provider_name_is_skipped(monkeypatch):
    monkeypatch.setattr(
        "src.data.registry.settings.data_provider_priority",
        "does_not_exist",
        raising=False,
    )
    reset_provider_cache()
    comp = get_provider()
    # Fail-safe: always ends up with at least yfinance.
    assert any(p.name == "yfinance" for p in comp.providers)
    reset_provider_cache()
