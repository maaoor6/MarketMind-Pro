"""Quant Engine Agent — technical analysis, Fibonacci, arbitrage signals."""

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime

import yfinance as yf
import pandas as pd

from src.database.cache import cache
from src.database.session import AsyncSessionLocal
from src.database.models import PriceHistory
from src.quant.indicators import generate_signals
from src.quant.fibonacci import calculate_fibonacci, FibonacciLevels
from src.quant.arbitrage import calculate_arbitrage, ArbitrageSignal, DUAL_LISTED
from src.utils.logger import get_logger
from src.utils.timezone_utils import market_status, now_utc

logger = get_logger(__name__)


@dataclass
class QuantSignal:
    ticker: str
    timestamp: str
    price: float
    signals: dict
    fibonacci: dict | None = None
    arbitrage: dict | None = None
    market_status: dict | None = None


class QuantEngine:
    """Autonomous agent for quantitative market analysis."""

    def __init__(self) -> None:
        self._running = False
        self._poll_interval = 60  # 1-minute polls

    async def fetch_price_data(
        self,
        ticker: str,
        period: str = "1y",
        interval: str = "1d",
    ) -> pd.DataFrame:
        """Fetch OHLCV data from yfinance with caching.

        Args:
            ticker: Ticker symbol.
            period: Data period (e.g., '1y', '6mo', '3mo').
            interval: Bar interval (e.g., '1d', '1h', '1m').

        Returns:
            DataFrame with OHLCV columns.
        """
        # Check Redis cache for 1-minute quotes
        if interval == "1m":
            cached = await cache.get_quote(ticker)
            if cached:
                logger.debug("quote_cache_hit", ticker=ticker)
                return pd.DataFrame(cached)

        loop = asyncio.get_event_loop()
        df = await loop.run_in_executor(
            None,
            lambda: yf.download(ticker, period=period, interval=interval, auto_adjust=True, progress=False),
        )

        if df.empty:
            raise ValueError(f"No data returned for {ticker}")

        # Cache 1-min quotes
        if interval == "1m":
            await cache.cache_quote(ticker, df.tail(10).to_dict())

        return df

    async def analyze(self, ticker: str) -> QuantSignal:
        """Run full quantitative analysis for a ticker.

        Returns a QuantSignal dataclass with all indicators.
        """
        logger.info("quant_analysis_start", ticker=ticker)

        # Fetch daily data (1 year for proper MA calculations)
        df = await self.fetch_price_data(ticker, period="1y", interval="1d")
        closes = df["Close"].squeeze()
        volumes = df["Volume"].squeeze()

        # Generate technical signals
        signals = generate_signals(closes, volumes)

        # Fibonacci from 52-week H/L
        fib_levels: FibonacciLevels = calculate_fibonacci(closes, ticker=ticker)
        fib_dict = {
            "high_52w": fib_levels.high_52w,
            "low_52w": fib_levels.low_52w,
            "current_price": fib_levels.current_price,
            "trend": fib_levels.trend,
            "retracements": fib_levels.retracements,
            "extensions": fib_levels.extensions,
            "nearest_support": fib_levels.nearest_support,
            "nearest_resistance": fib_levels.nearest_resistance,
        }

        # Arbitrage (only for dual-listed stocks)
        arb_dict = None
        if ticker.upper() in DUAL_LISTED:
            tase_ticker = DUAL_LISTED[ticker.upper()]
            try:
                tase_df = await self.fetch_price_data(tase_ticker, period="5d", interval="1d")
                tase_price = float(tase_df["Close"].iloc[-1])
                arb_signal = await calculate_arbitrage(
                    ticker_us=ticker.upper(),
                    price_us_usd=signals["price"],
                    price_tase_ils=tase_price,
                )
                arb_dict = {
                    "ticker_tase": arb_signal.ticker_tase,
                    "gap_pct": arb_signal.gap_pct,
                    "gap_direction": arb_signal.gap_direction,
                    "is_opportunity": arb_signal.is_opportunity,
                    "price_tase_in_usd": arb_signal.price_tase_in_usd,
                    "usd_ils_rate": arb_signal.usd_ils_rate,
                }
            except Exception as exc:
                logger.warning("arbitrage_calc_failed", ticker=ticker, error=str(exc))

        result = QuantSignal(
            ticker=ticker,
            timestamp=now_utc().isoformat(),
            price=signals["price"],
            signals=signals,
            fibonacci=fib_dict,
            arbitrage=arb_dict,
            market_status=market_status(),
        )

        logger.info(
            "quant_analysis_complete",
            ticker=ticker,
            price=signals["price"],
            rsi=signals.get("rsi"),
        )
        return result

    async def health_check(self) -> dict[str, str]:
        """Agent health check."""
        try:
            # Quick test with a liquid US stock
            df = await self.fetch_price_data("SPY", period="5d", interval="1d")
            if df.empty:
                return {"status": "error", "detail": "yfinance returned empty data"}
            return {"status": "ok", "detail": f"yfinance OK — {len(df)} bars fetched"}
        except Exception as exc:
            return {"status": "error", "detail": str(exc)}

    async def run_loop(self) -> None:
        """Continuous analysis loop — runs every minute during market hours."""
        self._running = True
        logger.info("quant_engine_started")
        while self._running:
            status = market_status()
            if status["nyse_open"] or status["tase_open"]:
                logger.debug("market_open_polling")
                # In production, iterate over watched tickers from DB
            await asyncio.sleep(self._poll_interval)

    def stop(self) -> None:
        self._running = False
        logger.info("quant_engine_stopped")
