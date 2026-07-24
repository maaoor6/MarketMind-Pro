"""yfinance-backed provider — the default, free, no-key market-data source.

This wraps the exact yfinance calls that previously lived inline in
``QuantEngine`` so behaviour is unchanged; it just moves them behind the
:class:`~src.data.provider.MarketDataProvider` interface. Caching stays in the
caller (``QuantEngine``), so this class is pure fetch.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pandas as pd
import yfinance as yf

from src.data.provider import (
    CAP_LIVE_PRICE,
    CAP_OHLCV,
    CAP_SCREEN,
    MarketDataProvider,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


class YFinanceProvider(MarketDataProvider):
    """Yahoo Finance via the ``yfinance`` package (unofficial, free)."""

    name = "yfinance"
    capabilities = frozenset({CAP_OHLCV, CAP_LIVE_PRICE, CAP_SCREEN})

    def __init__(self, screen_fn: Callable[..., dict] | None = None) -> None:
        # Injectable for tests; defaults to the real yfinance screener.
        self._screen_fn = screen_fn or yf.screen

    async def fetch_ohlcv(
        self,
        ticker: str,
        period: str = "1y",
        interval: str = "1d",
    ) -> pd.DataFrame:
        """Download OHLCV bars (auto-adjusted). Raises ValueError if empty."""
        df: pd.DataFrame = await asyncio.to_thread(
            lambda: yf.download(
                ticker,
                period=period,
                interval=interval,
                auto_adjust=True,
                progress=False,
            )
        )
        if df.empty:
            raise ValueError(f"No data returned for {ticker}")
        # Flatten MultiIndex columns yfinance returns for a single ticker.
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df

    async def fetch_live_price(self, ticker: str) -> tuple[float, float | None]:
        """Return ``(last_price, prev_close)`` via ``fast_info`` (all sessions)."""
        fi = await asyncio.to_thread(lambda: yf.Ticker(ticker).fast_info)
        last_price = getattr(fi, "last_price", None)
        prev_close = getattr(fi, "previous_close", None)
        if last_price is None:
            raise ValueError(f"No live price available for {ticker}")
        return float(last_price), (
            float(prev_close) if prev_close is not None else None
        )

    async def screen(self, screen_name: str, count: int = 25) -> dict:
        """Run a Yahoo predefined screener (e.g. ``day_gainers``)."""
        payload = await asyncio.to_thread(self._screen_fn, screen_name, count=count)
        return payload if isinstance(payload, dict) else {}

    async def health_check(self) -> dict[str, str]:
        """Probe by fetching a few SPY bars."""
        try:
            df = await self.fetch_ohlcv("SPY", period="5d", interval="1d")
            return {"status": "ok", "detail": f"yfinance OK — {len(df)} bars"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "detail": type(exc).__name__}
