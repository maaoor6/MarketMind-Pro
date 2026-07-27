"""Unit tests for the StockArena REST client (mocked httpx transport)."""

import json

import httpx
import pytest
from src.trading import stockarena_client as sac
from src.trading.stockarena_client import (
    FatalTradingError,
    StockArenaClient,
    StockArenaError,
    parse_portfolio,
)


def _make_client(handler) -> StockArenaClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        transport=transport,
        base_url="https://test.local/stock_arena",
        headers={"Authorization": "Bearer test-token"},
    )
    return StockArenaClient(client=http_client)


def _ok(data: dict) -> httpx.Response:
    return httpx.Response(200, json={"success": True, "data": data})


def _err(code: str, status: int = 400, **extra) -> httpx.Response:
    return httpx.Response(
        status,
        json={"success": False, "error": {"code": code, "message": "boom", **extra}},
    )


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Retries should not actually wait during tests."""

    async def _instant(_seconds):
        return None

    monkeypatch.setattr(sac.asyncio, "sleep", _instant)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_success_envelope_unwrapped():
    client = _make_client(lambda request: _ok({"session": "regular", "is_open": True}))
    status = await client.market_status()
    assert status["session"] == "regular"
    assert status["is_open"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_error_envelope_raises_typed_error():
    client = _make_client(lambda request: _err("INSUFFICIENT_FUNDS"))
    with pytest.raises(StockArenaError) as excinfo:
        await client.get_portfolio()
    assert excinfo.value.code == "INSUFFICIENT_FUNDS"
    assert not isinstance(excinfo.value, FatalTradingError)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unauthorized_is_fatal_and_not_retried():
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        return _err("UNAUTHORIZED", status=401)

    client = _make_client(handler)
    with pytest.raises(FatalTradingError):
        await client.get_portfolio()
    assert calls["count"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limited_retried_then_succeeds():
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        if calls["count"] < 3:
            return _err("RATE_LIMITED", status=429, retry_after=0.01)
        return _ok({"cash": 10000, "total_value": 10000, "return_pct": 0})

    client = _make_client(handler)
    portfolio = await client.get_portfolio()
    assert portfolio.cash == 10000
    assert calls["count"] == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_market_data_unavailable_retried_with_backoff():
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        if calls["count"] == 1:
            return _err("MARKET_DATA_UNAVAILABLE", status=503)
        return _ok({"price": 123.45})

    client = _make_client(handler)
    quote = await client.get_quote("AAPL")
    assert quote["price"] == 123.45
    assert calls["count"] == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_place_trade_sends_expected_payload():
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return _ok(
            {
                "trade": {"fill_price": 200.5},
                "portfolio": {"cash": 7995, "total_value": 10000, "return_pct": 0},
            }
        )

    client = _make_client(handler)
    result = await client.place_trade("buy", "aapl", 10, extended_hours=True)
    assert captured["action"] == "buy"
    assert captured["ticker"] == "AAPL"
    assert captured["quantity"] == 10
    assert captured["extended_hours"] is True
    assert "bot_id" in captured and "user_id" in captured
    assert result.fill_price == 200.5
    assert result.portfolio is not None
    assert result.portfolio.cash == 7995


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("quantity", [0, -5, 1.5, True])
async def test_place_trade_rejects_invalid_quantity(quantity):
    client = _make_client(lambda request: _ok({}))
    with pytest.raises(ValueError, match="positive int"):
        await client.place_trade("buy", "AAPL", quantity)


@pytest.mark.unit
def test_parse_portfolio_handles_positions_and_missing_keys():
    portfolio = parse_portfolio(
        {
            "cash": "5000.5",
            "total_value": 11000,
            "return_pct": 10,
            "positions": [
                {
                    "ticker": "aapl",
                    "quantity": 10,
                    "avg_price": 200,
                    "current_price": 210,
                },
                {"quantity": 5},  # no ticker — skipped
                "garbage",  # not a dict — skipped
                {"ticker": "MSFT", "quantity": 0, "avg_price": 400},  # flat — skipped
            ],
        }
    )
    assert portfolio.cash == 5000.5
    assert list(portfolio.positions) == ["AAPL"]
    assert portfolio.positions["AAPL"].current_price == 210


@pytest.mark.unit
def test_parse_portfolio_reads_real_server_shape():
    """The live server returns avg_cost/price/market_value — must all map."""
    portfolio = parse_portfolio(
        {
            "cash": 3622.49,
            "total_value": 9999.36,
            "return_pct": -0.01,
            "positions": [
                {
                    "ticker": "AAPL",
                    "quantity": 4,
                    "avg_cost": 312.5,
                    "price": 312.44,
                    "market_value": 1249.76,
                }
            ],
        }
    )
    pos = portfolio.positions["AAPL"]
    assert pos.avg_price == 312.5
    assert pos.current_price == 312.44
    assert pos.market_value == 1249.76


@pytest.mark.unit
def test_parse_portfolio_empty_payload_is_safe():
    portfolio = parse_portfolio({})
    assert portfolio.cash == 0.0
    assert portfolio.positions == {}
