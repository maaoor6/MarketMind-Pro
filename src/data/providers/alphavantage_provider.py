"""Alpha Vantage provider — tertiary fallback (free tier, 25 req/day).

Alpha Vantage's free key is heavily rate-limited (25 requests/day), so this
provider is intended only as a **last-resort** quote / daily-OHLCV fallback —
place it last in ``DATA_PROVIDER_PRIORITY``. Fails open when
``ALPHA_VANTAGE_KEY`` is empty (all calls raise NotSupported → router fails
over). Rate-limit notices in the payload are surfaced as errors so the router
moves on rather than caching a throttle message.
"""

from __future__ import annotations

import httpx
import pandas as pd

from src.data.provider import (
    CAP_LIVE_PRICE,
    CAP_OHLCV,
    MarketDataProvider,
    NotSupportedError,
)
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

_BASE_URL = "https://www.alphavantage.co/query"


class AlphaVantageProvider(MarketDataProvider):
    """Free-tier Alpha Vantage quote + daily OHLCV (rate-limited fallback)."""

    name = "alphavantage"
    capabilities = frozenset({CAP_LIVE_PRICE, CAP_OHLCV})

    def __init__(self, api_key: str | None = None, timeout: float = 15.0) -> None:
        self._api_key = api_key if api_key is not None else settings.alpha_vantage_key
        self._timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    async def _get_json(self, params: dict) -> dict:
        query = {**params, "apikey": self._api_key}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(_BASE_URL, params=query)
            resp.raise_for_status()
            data = resp.json()
        if not isinstance(data, dict):
            raise ValueError("alphavantage: unexpected payload")
        # Free-tier throttle / error responses.
        if "Note" in data or "Information" in data or "Error Message" in data:
            raise ValueError("alphavantage: rate-limited or error response")
        return data

    async def fetch_live_price(self, ticker: str) -> tuple[float, float | None]:
        if not self.enabled:
            raise NotSupportedError("alphavantage: no API key configured")
        data = await self._get_json(
            {"function": "GLOBAL_QUOTE", "symbol": ticker.upper()}
        )
        quote = data.get("Global Quote", {})
        price = quote.get("05. price")
        prev = quote.get("08. previous close")
        if not price:
            raise ValueError(f"No live price available for {ticker}")
        return float(price), (float(prev) if prev else None)

    async def fetch_ohlcv(
        self,
        ticker: str,
        period: str = "1y",
        interval: str = "1d",
    ) -> pd.DataFrame:
        if not self.enabled:
            raise NotSupportedError("alphavantage: no API key configured")
        if interval != "1d":
            raise NotSupportedError(f"alphavantage: interval {interval!r} unsupported")
        # "compact" = last 100 bars; "full" for long history.
        outputsize = "full" if period in {"2y", "5y", "10y", "max"} else "compact"
        data = await self._get_json(
            {
                "function": "TIME_SERIES_DAILY",
                "symbol": ticker.upper(),
                "outputsize": outputsize,
            }
        )
        series = data.get("Time Series (Daily)")
        if not series:
            raise ValueError(f"No data returned for {ticker}")
        return self._parse_series(series)

    @staticmethod
    def _parse_series(series: dict) -> pd.DataFrame:
        """Convert an Alpha Vantage daily series to a yfinance-shaped frame."""
        rows = {
            pd.to_datetime(date): {
                "Open": float(v["1. open"]),
                "High": float(v["2. high"]),
                "Low": float(v["3. low"]),
                "Close": float(v["4. close"]),
                "Volume": int(v["5. volume"]),
            }
            for date, v in series.items()
        }
        df = pd.DataFrame.from_dict(rows, orient="index").sort_index()
        df.index.name = "Date"
        return df

    async def health_check(self) -> dict[str, str]:
        if not self.enabled:
            return {"status": "ok", "detail": "alphavantage disabled (no key)"}
        try:
            await self.fetch_live_price("IBM")
            return {"status": "ok", "detail": "alphavantage OK"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "detail": type(exc).__name__}
