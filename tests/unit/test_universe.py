"""Unit tests for the dynamic UniverseScanner — filters, fallback, pinning."""

import pytest
from src.trading import universe as universe_module
from src.trading.stockarena_client import Portfolio, Position
from src.trading.universe import UniverseScanner, _passes_filters
from src.utils.config import settings


class FakeCache:
    """In-memory stand-in for the Redis cache singleton."""

    def __init__(self) -> None:
        self.store: dict[str, object] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ttl=None):
        self.store[key] = value

    async def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture
def fake_cache(monkeypatch) -> FakeCache:
    fake = FakeCache()
    monkeypatch.setattr(universe_module, "cache", fake)
    return fake


@pytest.fixture(autouse=True)
def _dynamic_universe_on(monkeypatch):
    # Tests must not depend on the developer's .env (the live deployment
    # currently sets TRADING_DYNAMIC_UNIVERSE=false); dynamic-path tests
    # assume it is on, and the static-mode test overrides it explicitly.
    monkeypatch.setattr(settings, "trading_dynamic_universe", True)


def quote(
    symbol: str,
    price: float = 100.0,
    volume: float = 5_000_000,
    market_cap: float = 50e9,
    quote_type: str = "EQUITY",
) -> dict:
    return {
        "symbol": symbol,
        "regularMarketPrice": price,
        "averageDailyVolume3Month": volume,
        "marketCap": market_cap,
        "quoteType": quote_type,
    }


def make_portfolio(positions: dict[str, Position] | None = None) -> Portfolio:
    return Portfolio(
        cash=10000.0, total_value=10000.0, return_pct=0.0, positions=positions or {}
    )


def fake_screen_factory(payloads: dict[str, dict]):
    def fake_screen(name, count=25):
        if name not in payloads:
            raise RuntimeError(f"screener {name} unavailable")
        return payloads[name]

    return fake_screen


# ── Filters ────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_filters_accept_liquid_large_cap():
    assert _passes_filters(quote("AAPL"), max_price=None)


@pytest.mark.unit
def test_filters_reject_penny_stock():
    assert not _passes_filters(quote("PLUG", price=2.5), max_price=None)


@pytest.mark.unit
def test_filters_reject_illiquid_and_small_cap():
    assert not _passes_filters(quote("SEM", volume=200_000), max_price=None)
    assert not _passes_filters(quote("CRNX", market_cap=500e6), max_price=None)


@pytest.mark.unit
def test_filters_reject_unaffordable_share_price():
    # Price above the per-position budget → can't buy even 1 share.
    assert not _passes_filters(quote("MU", price=3000.0), max_price=2000.0)


@pytest.mark.unit
def test_filters_reject_weird_symbols_and_quote_types():
    assert not _passes_filters(quote("BRK.A"), max_price=None)
    assert not _passes_filters(quote("SPY-W"), max_price=None)
    assert not _passes_filters(quote("AAPL", quote_type="FUTURE"), max_price=None)


@pytest.mark.unit
def test_filters_allow_etf_without_market_cap():
    q = quote("QQQ", quote_type="ETF")
    q["marketCap"] = None
    assert _passes_filters(q, max_price=None)


# ── Scanner ────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_universe_pins_spy_and_positions_first(fake_cache):
    scanner = UniverseScanner(
        screen_fn=fake_screen_factory(
            {
                "day_gainers": {"quotes": [quote("NVDA"), quote("AMD")]},
                "most_actives": {"quotes": [quote("INTC")]},
                "day_losers": {"quotes": []},
            }
        )
    )
    portfolio = make_portfolio(
        {"TSLA": Position(ticker="TSLA", quantity=3, avg_price=200.0)}
    )
    universe = await scanner.get_universe(portfolio)
    assert universe[:2] == ["SPY", "TSLA"]
    assert {"NVDA", "AMD", "INTC"} <= set(universe)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_universe_dedupes_across_screeners(fake_cache):
    scanner = UniverseScanner(
        screen_fn=fake_screen_factory(
            {
                "day_gainers": {"quotes": [quote("NVDA")]},
                "most_actives": {"quotes": [quote("NVDA"), quote("AMD")]},
                "day_losers": {"quotes": [quote("AMD")]},
            }
        )
    )
    universe = await scanner.get_universe(make_portfolio())
    assert universe.count("NVDA") == 1
    assert universe.count("AMD") == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_universe_capped_at_configured_size(fake_cache, monkeypatch):
    monkeypatch.setattr(settings, "trading_universe_size", 5)
    quotes = [quote(f"TK{chr(ord('A') + i)}") for i in range(20)]
    scanner = UniverseScanner(
        screen_fn=fake_screen_factory(
            {
                "day_gainers": {"quotes": quotes},
                "most_actives": {"quotes": []},
                "day_losers": {"quotes": []},
            }
        )
    )
    universe = await scanner.get_universe(make_portfolio())
    assert len(universe) == 5


@pytest.mark.unit
@pytest.mark.asyncio
async def test_universe_falls_back_to_watchlist_when_scan_fails(fake_cache):
    scanner = UniverseScanner(screen_fn=fake_screen_factory({}))
    universe = await scanner.get_universe(make_portfolio())
    assert universe[0] == "SPY"
    for ticker in universe[1:]:
        assert ticker in settings.trading_watchlist


@pytest.mark.unit
@pytest.mark.asyncio
async def test_universe_static_mode_uses_watchlist(fake_cache, monkeypatch):
    monkeypatch.setattr(settings, "trading_dynamic_universe", False)

    def explode(*args, **kwargs):
        raise AssertionError("screener must not be called in static mode")

    scanner = UniverseScanner(screen_fn=explode)
    universe = await scanner.get_universe(make_portfolio())
    assert universe[0] == "SPY"
    assert set(universe) <= {"SPY", *settings.trading_watchlist}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_universe_uses_cached_scan(fake_cache):
    fake_cache.store[universe_module.UNIVERSE_CACHE_KEY] = ["NVDA", "AMD"]

    def explode(*args, **kwargs):
        raise AssertionError("screener must not be called when cache is warm")

    scanner = UniverseScanner(screen_fn=explode)
    universe = await scanner.get_universe(make_portfolio())
    # SPY pinned first; the stable watchlist core takes its reserved share,
    # then the cached dynamic candidates fill the rest.
    assert universe[0] == "SPY"
    assert "NVDA" in universe and "AMD" in universe
    assert any(t in settings.trading_watchlist for t in universe[1:])
    assert len(universe) <= settings.trading_universe_size


@pytest.mark.unit
def test_quality_gate_rejects_below_sma200():
    below = quote("F")
    below["twoHundredDayAverage"] = 120.0  # price 100 < SMA200 → downtrend
    assert not _passes_filters(below, max_price=None)
    above = quote("AAPL")
    above["twoHundredDayAverage"] = 90.0
    assert _passes_filters(above, max_price=None)
    # Fail-open when the screener doesn't supply the average.
    assert _passes_filters(quote("MSFT"), max_price=None)


@pytest.mark.unit
def test_day_losers_screener_removed():
    # Negative selection: the day's crashers must not feed the agent.
    assert "day_losers" not in universe_module._SCREENERS


@pytest.mark.unit
def test_merge_with_core_reserves_watchlist_share():
    core = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH", "III", "JJJ"]
    dynamic = [f"D{i}" for i in range(30)]
    universe = UniverseScanner.merge_with_core(["SPY"], dynamic, core)
    assert universe[0] == "SPY"
    core_count = sum(1 for t in universe if t in core)
    free = settings.trading_universe_size - 1
    assert core_count == round(free * settings.universe_quality_ratio)
    assert len(universe) == settings.trading_universe_size


@pytest.mark.unit
@pytest.mark.asyncio
async def test_partial_screener_failure_still_returns_survivors(fake_cache):
    scanner = UniverseScanner(
        screen_fn=fake_screen_factory(
            {"most_actives": {"quotes": [quote("INTC"), quote("NU", price=14.0)]}}
        )
    )
    universe = await scanner.get_universe(make_portfolio())
    assert "INTC" in universe
    assert "NU" in universe
