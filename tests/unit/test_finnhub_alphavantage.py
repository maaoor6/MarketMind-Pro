"""Unit tests for the Finnhub + Alpha Vantage providers (no network)."""

import pytest
from src.data.provider import CAP_LIVE_PRICE, CAP_OHLCV, NotSupportedError
from src.data.providers.alphavantage_provider import AlphaVantageProvider
from src.data.providers.finnhub_provider import FinnhubProvider

# ── Finnhub ──────────────────────────────────────────────────────────────────


def test_finnhub_disabled_without_key():
    assert not FinnhubProvider(api_key="").enabled


@pytest.mark.asyncio
async def test_finnhub_no_key_fails_over():
    with pytest.raises(NotSupportedError):
        await FinnhubProvider(api_key="").fetch_live_price("AAPL")


@pytest.mark.asyncio
async def test_finnhub_quote_parsing(monkeypatch):
    p = FinnhubProvider(api_key="k")

    async def _fake_get(path, params):
        assert path == "/quote"
        return {"c": 190.5, "pc": 188.0}

    monkeypatch.setattr(p, "_get_json", _fake_get)
    price, prev = await p.fetch_live_price("AAPL")
    assert price == 190.5
    assert prev == 188.0


@pytest.mark.asyncio
async def test_finnhub_unknown_symbol_raises(monkeypatch):
    p = FinnhubProvider(api_key="k")

    async def _fake_get(path, params):
        return {"c": 0, "pc": 0}  # finnhub sentinel for unknown

    monkeypatch.setattr(p, "_get_json", _fake_get)
    with pytest.raises(ValueError):
        await p.fetch_live_price("ZZZZ")


@pytest.mark.asyncio
async def test_finnhub_news_empty_without_key():
    news = await FinnhubProvider(api_key="").company_news(
        "AAPL", "2026-01-01", "2026-01-02"
    )
    assert news == []


@pytest.mark.asyncio
async def test_finnhub_earnings_calendar_parsing(monkeypatch):
    p = FinnhubProvider(api_key="k")

    async def _fake_get(path, params):
        return {"earningsCalendar": [{"symbol": "AAPL", "date": "2026-01-30"}]}

    monkeypatch.setattr(p, "_get_json", _fake_get)
    rows = await p.earnings_calendar("2026-01-01", "2026-02-01")
    assert rows[0]["symbol"] == "AAPL"


# ── Alpha Vantage ────────────────────────────────────────────────────────────


def test_alphavantage_capabilities():
    p = AlphaVantageProvider(api_key="k")
    assert p.supports(CAP_LIVE_PRICE)
    assert p.supports(CAP_OHLCV)


@pytest.mark.asyncio
async def test_alphavantage_no_key_fails_over():
    with pytest.raises(NotSupportedError):
        await AlphaVantageProvider(api_key="").fetch_live_price("IBM")


@pytest.mark.asyncio
async def test_alphavantage_quote_parsing(monkeypatch):
    p = AlphaVantageProvider(api_key="k")

    async def _fake(params):
        return {"Global Quote": {"05. price": "150.25", "08. previous close": "149.00"}}

    monkeypatch.setattr(p, "_get_json", _fake)
    price, prev = await p.fetch_live_price("IBM")
    assert price == 150.25
    assert prev == 149.0


@pytest.mark.asyncio
async def test_alphavantage_ohlcv_parsing(monkeypatch):
    p = AlphaVantageProvider(api_key="k")

    async def _fake(params):
        return {
            "Time Series (Daily)": {
                "2026-07-22": {
                    "1. open": "10.0",
                    "2. high": "11.0",
                    "3. low": "9.5",
                    "4. close": "10.5",
                    "5. volume": "1000",
                },
                "2026-07-23": {
                    "1. open": "10.5",
                    "2. high": "12.0",
                    "3. low": "10.2",
                    "4. close": "11.8",
                    "5. volume": "2000",
                },
            }
        }

    monkeypatch.setattr(p, "_get_json", _fake)
    df = await p.fetch_ohlcv("IBM")
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    # Sorted ascending → last row is the newer date.
    assert float(df["Close"].iloc[-1]) == 11.8
    assert df.index.name == "Date"


@pytest.mark.asyncio
async def test_alphavantage_rate_limit_raises(monkeypatch):
    p = AlphaVantageProvider(api_key="k")

    async def _fake_http(params):
        # Simulate the real throttle payload surfacing through _get_json.
        raise ValueError("alphavantage: rate-limited or error response")

    monkeypatch.setattr(p, "_get_json", _fake_http)
    with pytest.raises(ValueError):
        await p.fetch_live_price("IBM")
