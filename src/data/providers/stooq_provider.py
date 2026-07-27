"""Stooq-backed provider — free daily OHLCV via CSV, no API key.

Stooq (``stooq.com``) publishes end-of-day OHLCV as plain CSV. It carries no
authentication and needs no key, which makes it the ideal **independent
cross-check** for yfinance prices (Phase 1 validation) and a fail-over source
for the OHLCV plane. Only US common stock / ETF symbols (no exchange suffix)
are handled — those map to Stooq's ``<symbol>.us`` namespace; anything already
carrying a dotted suffix (e.g. ``TEVA.TA``) raises NotSupported so the router
falls back to yfinance rather than risk pulling a mismatched foreign listing.

Intraday intervals are not served (Stooq's free feed is EOD) — those also
fail over. This provider is deliberately OHLCV-only.
"""

from __future__ import annotations

import io

import httpx
import pandas as pd

from src.data.provider import CAP_OHLCV, MarketDataProvider, NotSupportedError
from src.utils.logger import get_logger
from src.utils.timezone_utils import now_utc

logger = get_logger(__name__)

_BASE_URL = "https://stooq.com/q/d/l/"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,text/plain,*/*",
}

# yfinance interval → Stooq interval code (only EOD supported).
_INTERVAL_MAP = {"1d": "d", "1wk": "w", "1mo": "m"}

# yfinance period → approximate calendar days back (None = full history).
_PERIOD_DAYS = {
    "5d": 5,
    "1mo": 31,
    "3mo": 93,
    "6mo": 186,
    "1y": 366,
    "2y": 731,
    "5y": 1827,
    "10y": 3653,
    "ytd": 366,
    "max": None,
}


class StooqProvider(MarketDataProvider):
    """Free end-of-day OHLCV from Stooq (US symbols, CSV, no key)."""

    name = "stooq"
    capabilities = frozenset({CAP_OHLCV})

    def __init__(self, timeout: float = 12.0) -> None:
        self._timeout = timeout

    @staticmethod
    def _to_stooq_symbol(ticker: str) -> str:
        """Map a plain US ticker to Stooq's ``<symbol>.us`` namespace.

        Raises:
            NotSupportedError: for dotted (non-US-listed) symbols.
        """
        t = ticker.strip().upper()
        if "." in t:
            raise NotSupportedError(f"stooq: non-US symbol {ticker!r} not handled")
        return f"{t.lower()}.us"

    async def fetch_ohlcv(
        self,
        ticker: str,
        period: str = "1y",
        interval: str = "1d",
    ) -> pd.DataFrame:
        code = _INTERVAL_MAP.get(interval)
        if code is None:
            raise NotSupportedError(f"stooq: interval {interval!r} not supported")

        symbol = self._to_stooq_symbol(ticker)
        params: dict[str, str] = {"s": symbol, "i": code}
        days = _PERIOD_DAYS.get(period, 366)
        if days is not None:
            end = now_utc()
            start = end - pd.Timedelta(days=days)
            params["d1"] = start.strftime("%Y%m%d")
            params["d2"] = end.strftime("%Y%m%d")

        async with httpx.AsyncClient(timeout=self._timeout, headers=_HEADERS) as client:
            resp = await client.get(_BASE_URL, params=params)
            resp.raise_for_status()
            text = resp.text

        df = self._parse_csv(text, ticker)
        if df.empty:
            raise ValueError(f"No data returned for {ticker}")
        return df

    @staticmethod
    def _parse_csv(text: str, ticker: str) -> pd.DataFrame:
        """Parse a Stooq CSV response into a yfinance-shaped OHLCV frame."""
        stripped = text.strip()
        # Stooq returns a bare "No data" line (or an HTML error) when it has none.
        if not stripped or "No data" in stripped[:64] or "<" == stripped[:1]:
            return pd.DataFrame()
        try:
            raw = pd.read_csv(io.StringIO(text))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "stooq_parse_failed", ticker=ticker, error=type(exc).__name__
            )
            return pd.DataFrame()
        if "Date" not in raw.columns or "Close" not in raw.columns:
            return pd.DataFrame()
        raw["Date"] = pd.to_datetime(raw["Date"], errors="coerce")
        raw = raw.dropna(subset=["Date"]).set_index("Date")
        raw.index.name = "Date"
        if "Volume" not in raw.columns:
            raw["Volume"] = 0
        cols = ["Open", "High", "Low", "Close", "Volume"]
        return raw[[c for c in cols if c in raw.columns]]

    async def health_check(self) -> dict[str, str]:
        try:
            df = await self.fetch_ohlcv("SPY", period="5d", interval="1d")
            return {"status": "ok", "detail": f"stooq OK — {len(df)} bars"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "detail": type(exc).__name__}
