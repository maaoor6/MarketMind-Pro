"""Typed async client for the StockArena paper-trading REST API.

All money on this account is simulated. The API token is read from
``settings.stock_arena_token`` (env only) and is never logged.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

_RETRYABLE_CODES = {"RATE_LIMITED", "MARKET_DATA_UNAVAILABLE"}
_FATAL_CODES = {"UNAUTHORIZED", "FORBIDDEN", "BOT_INACTIVE"}


class StockArenaError(Exception):
    """A failed StockArena API call (success=false or transport failure)."""

    def __init__(
        self, code: str, message: str, retry_after: float | None = None
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retry_after = retry_after


class FatalTradingError(StockArenaError):
    """Auth/account errors — the agent must stop trading, never retry."""


class TradeTimeoutError(StockArenaError):
    """A trade POST timed out — the order MAY have executed server-side.

    Callers must reconcile against trade history instead of retrying.
    """

    def __init__(self, message: str) -> None:
        super().__init__("TIMEOUT", message)


@dataclass
class Position:
    ticker: str
    quantity: int
    avg_price: float
    current_price: float | None = None
    market_value: float | None = None


@dataclass
class Portfolio:
    cash: float
    total_value: float
    return_pct: float
    positions: dict[str, Position] = field(default_factory=dict)


@dataclass
class TradeResult:
    ticker: str
    action: str
    quantity: int
    fill_price: float | None
    portfolio: Portfolio | None
    raw: dict


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_position(item: dict) -> Position | None:
    ticker = str(item.get("ticker") or item.get("symbol") or "").upper()
    if not ticker:
        return None
    quantity = int(_as_float(item.get("quantity") or item.get("shares")))
    avg_price = _as_float(
        item.get("avg_price") or item.get("avg_cost") or item.get("average_price")
    )
    current = item.get("current_price") or item.get("price")
    market_value = item.get("market_value") or item.get("value")
    return Position(
        ticker=ticker,
        quantity=quantity,
        avg_price=avg_price,
        current_price=_as_float(current) if current is not None else None,
        market_value=_as_float(market_value) if market_value is not None else None,
    )


def parse_portfolio(data: dict) -> Portfolio:
    """Build a Portfolio from an API payload, tolerating missing keys."""
    raw_positions = data.get("positions") or data.get("holdings") or []
    positions: dict[str, Position] = {}
    for item in raw_positions:
        if not isinstance(item, dict):
            continue
        pos = _parse_position(item)
        if pos and pos.quantity > 0:
            positions[pos.ticker] = pos
    return Portfolio(
        cash=_as_float(data.get("cash")),
        total_value=_as_float(data.get("total_value")),
        return_pct=_as_float(data.get("return_pct")),
        positions=positions,
    )


class StockArenaClient:
    """Async REST client with envelope unwrapping, retry, and typed errors."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._owns_client = client is None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=settings.stock_arena_base_url,
                timeout=15,
                headers={"Authorization": f"Bearer {settings.stock_arena_token}"},
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client if this instance owns it."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        path: str,
        json_body: dict | None = None,
        max_retries: int = 3,
    ) -> dict:
        """Perform a request, unwrap the success/error envelope, retry politely.

        Raises:
            FatalTradingError: On UNAUTHORIZED / FORBIDDEN / BOT_INACTIVE.
            TradeTimeoutError: On timeout of a trade POST (may have executed).
            StockArenaError: On any other API error or transport failure.
        """
        client = self._get_client()
        last_error: StockArenaError | None = None

        for attempt in range(max_retries + 1):
            try:
                resp = await client.request(method, path, json=json_body)
            except httpx.TimeoutException as exc:
                if method == "POST" and "/trades" in path:
                    raise TradeTimeoutError(str(exc)) from exc
                last_error = StockArenaError("NETWORK_ERROR", str(exc))
                await asyncio.sleep(2**attempt)
                continue
            except httpx.HTTPError as exc:
                last_error = StockArenaError("NETWORK_ERROR", str(exc))
                await asyncio.sleep(2**attempt)
                continue

            try:
                body = resp.json()
            except ValueError as exc:
                raise StockArenaError(
                    "INVALID_RESPONSE", f"non-JSON response (HTTP {resp.status_code})"
                ) from exc

            if isinstance(body, dict) and body.get("success"):
                data = body.get("data")
                return data if isinstance(data, dict) else {"result": data}

            error = body.get("error") if isinstance(body, dict) else None
            error = error if isinstance(error, dict) else {}
            code = str(error.get("code") or f"HTTP_{resp.status_code}")
            message = str(error.get("message") or resp.text[:200])
            retry_after = error.get("retry_after")
            if retry_after is None:
                retry_after = resp.headers.get("Retry-After")
            retry_seconds = _as_float(retry_after, default=0) or None

            if resp.status_code == 429:
                code = "RATE_LIMITED"

            if code in _FATAL_CODES:
                logger.error("stockarena_fatal_error", code=code, path=path)
                raise FatalTradingError(code, message)

            if code in _RETRYABLE_CODES and attempt < max_retries:
                delay = retry_seconds if retry_seconds else float(2**attempt)
                logger.warning(
                    "stockarena_retryable_error",
                    code=code,
                    path=path,
                    attempt=attempt,
                    delay=delay,
                )
                await asyncio.sleep(delay)
                last_error = StockArenaError(code, message, retry_seconds)
                continue

            logger.warning("stockarena_api_error", code=code, path=path)
            raise StockArenaError(code, message, retry_seconds)

        raise last_error or StockArenaError("UNKNOWN", "request failed")

    # ── API methods ───────────────────────────────────────────────────

    async def market_status(self) -> dict:
        """GET /market-status → session, is_open, is_holiday, next_open_at."""
        return await self._request("GET", "/api/v1/market-status")

    async def get_portfolio(self) -> Portfolio:
        """GET the bot's portfolio: cash, total_value, return_pct, positions."""
        data = await self._request(
            "GET", f"/api/v1/bots/{settings.stock_arena_bot_id}/portfolio"
        )
        return parse_portfolio(data)

    async def get_trades(self) -> list[dict]:
        """GET the bot's recent trade history."""
        data = await self._request(
            "GET", f"/api/v1/bots/{settings.stock_arena_bot_id}/trades"
        )
        trades = data.get("trades") or data.get("result") or []
        return trades if isinstance(trades, list) else []

    async def get_quote(self, ticker: str) -> dict:
        """GET a live quote for a symbol."""
        return await self._request("GET", f"/api/v1/quotes/{ticker.upper()}")

    async def place_trade(
        self,
        action: Literal["buy", "sell"],
        ticker: str,
        quantity: int,
        extended_hours: bool = False,
    ) -> TradeResult:
        """POST a market order. Server fills at the live quote.

        Raises:
            ValueError: If quantity is not a positive integer.
        """
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
            raise ValueError(f"quantity must be a positive int, got {quantity!r}")
        payload = {
            "action": action,
            "ticker": ticker.upper(),
            "quantity": quantity,
            "extended_hours": extended_hours,
            "bot_id": settings.stock_arena_bot_id,
            "user_id": settings.stock_arena_user_id,
        }
        data = await self._request("POST", "/api/v1/trades", json_body=payload)
        trade = data.get("trade") if isinstance(data.get("trade"), dict) else data
        fill = trade.get("fill_price") or trade.get("price")
        portfolio_data = data.get("portfolio")
        return TradeResult(
            ticker=ticker.upper(),
            action=action,
            quantity=quantity,
            fill_price=_as_float(fill) if fill is not None else None,
            portfolio=(
                parse_portfolio(portfolio_data)
                if isinstance(portfolio_data, dict)
                else None
            ),
            raw=data,
        )

    async def health_check(self) -> dict[str, str]:
        """Standard agent health dict. Token must be configured."""
        if not settings.stock_arena_token:
            return {"status": "error", "detail": "STOCK_ARENA_TOKEN not set"}
        try:
            status = await self.market_status()
            return {
                "status": "ok",
                "detail": f"StockArena OK — session={status.get('session')}",
            }
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "detail": str(exc)}
