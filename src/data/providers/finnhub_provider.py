"""Finnhub-backed provider — free-tier quote / company-news / earnings.

Finnhub (``finnhub.io``) offers a free API key with real-time-ish quotes,
company news and an earnings calendar. Historical candles are paywalled for US
equities, so this provider serves **live quotes** (an independent fail-over for
the price plane) plus two provider-specific helpers used later by the Phase-2
SentimentAgent (``company_news``) and the EarningsDrift strategy / macro
blackout calendar (``earnings_calendar``).

The key is sent in the ``X-Finnhub-Token`` header — never in the URL — so it
cannot leak through request-line logging. When ``FINNHUB_API_KEY`` is empty the
provider fails open: quote calls raise NotSupported (router fails over) and the
helpers return empty lists.
"""

from __future__ import annotations

import httpx

from src.data.provider import CAP_LIVE_PRICE, MarketDataProvider, NotSupportedError
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

_BASE_URL = "https://finnhub.io/api/v1"


class FinnhubProvider(MarketDataProvider):
    """Free-tier Finnhub quote + news + earnings calendar (key via header)."""

    name = "finnhub"
    capabilities = frozenset({CAP_LIVE_PRICE})

    def __init__(self, api_key: str | None = None, timeout: float = 12.0) -> None:
        self._api_key = api_key if api_key is not None else settings.finnhub_api_key
        self._timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    async def _get_json(self, path: str, params: dict) -> dict | list:
        """GET a Finnhub endpoint with the key in a header. Raises on HTTP error."""
        headers = {"X-Finnhub-Token": self._api_key, "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
            resp = await client.get(f"{_BASE_URL}{path}", params=params)
            resp.raise_for_status()
            return resp.json()

    async def fetch_live_price(self, ticker: str) -> tuple[float, float | None]:
        if not self.enabled:
            raise NotSupportedError("finnhub: no API key configured")
        data = await self._get_json("/quote", {"symbol": ticker.upper()})
        if not isinstance(data, dict):
            raise ValueError(f"finnhub: bad quote payload for {ticker}")
        current = data.get("c")
        prev = data.get("pc")
        # Finnhub returns 0 for an unknown/unsupported symbol.
        if not current:
            raise ValueError(f"No live price available for {ticker}")
        return float(current), (float(prev) if prev else None)

    async def company_news(
        self, ticker: str, from_date: str, to_date: str
    ) -> list[dict]:
        """Return company-news items (empty on any failure). Provider-specific."""
        if not self.enabled:
            return []
        try:
            data = await self._get_json(
                "/company-news",
                {"symbol": ticker.upper(), "from": from_date, "to": to_date},
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("finnhub_news_failed", ticker=ticker, error=type(exc).__name__)
            return []
        return data if isinstance(data, list) else []

    async def earnings_calendar(self, from_date: str, to_date: str) -> list[dict]:
        """Return earnings-calendar rows for a date range (empty on failure)."""
        if not self.enabled:
            return []
        try:
            data = await self._get_json(
                "/calendar/earnings", {"from": from_date, "to": to_date}
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("finnhub_earnings_failed", error=type(exc).__name__)
            return []
        if isinstance(data, dict):
            rows = data.get("earningsCalendar", [])
            return rows if isinstance(rows, list) else []
        return []

    async def health_check(self) -> dict[str, str]:
        if not self.enabled:
            return {"status": "ok", "detail": "finnhub disabled (no key)"}
        try:
            await self.fetch_live_price("AAPL")
            return {"status": "ok", "detail": "finnhub OK"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "detail": type(exc).__name__}
