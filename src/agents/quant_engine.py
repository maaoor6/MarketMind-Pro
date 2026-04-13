"""Quant Engine Agent — technical analysis, Fibonacci, arbitrage signals."""

import asyncio
import dataclasses
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
import yfinance as yf
from sqlalchemy import select

from src.database.cache import cache
from src.database.models import PriceHistory
from src.database.session import AsyncSessionLocal
from src.quant.fibonacci import FibonacciLevels, calculate_fibonacci
from src.quant.indicators import generate_signals
from src.utils.logger import get_logger
from src.utils.timezone_utils import market_status, now_utc

logger = get_logger(__name__)

# Tickers polled every minute during market hours
_WATCHLIST_BASE = ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "SPY", "QQQ", "TSLA"]


@dataclass
class QuantSignal:
    ticker: str
    timestamp: str
    price: float
    signals: dict
    fibonacci: dict | None = None
    market_status: dict | None = None
    ohlcv: dict | None = None  # last bar OHLCV for DB persistence


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
        if interval == "1m":
            cached = await cache.get_quote(ticker)
            if cached:
                logger.debug("quote_cache_hit", ticker=ticker)
                return pd.DataFrame(cached)

        loop = asyncio.get_event_loop()
        df: pd.DataFrame = await loop.run_in_executor(
            None,
            lambda: yf.download(
                ticker,
                period=period,
                interval=interval,
                auto_adjust=True,
                progress=False,
            ),
        )

        if df.empty:
            raise ValueError(f"No data returned for {ticker}")

        # Flatten MultiIndex columns when yfinance returns them for a single ticker
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        if interval == "1m":
            await cache.cache_quote(ticker, df.tail(10).to_dict())

        return df

    async def fetch_live_price(self, ticker: str) -> tuple[float, float | None]:
        """Return (live_price, prev_close) using yfinance fast_info.

        Works across all sessions: pre-market, regular, and after-hours.

        Args:
            ticker: Ticker symbol.

        Returns:
            Tuple of (last_price, previous_close). prev_close may be None.

        Raises:
            ValueError: If no live price is available.
        """
        loop = asyncio.get_event_loop()
        fi = await loop.run_in_executor(None, lambda: yf.Ticker(ticker).fast_info)
        last_price = getattr(fi, "last_price", None)
        prev_close = getattr(fi, "previous_close", None)
        if last_price is None:
            raise ValueError(f"No live price available for {ticker}")
        return float(last_price), (
            float(prev_close) if prev_close is not None else None
        )

    async def analyze(self, ticker: str) -> QuantSignal:
        """Run full quantitative analysis for a ticker.

        Args:
            ticker: Ticker symbol.

        Returns:
            QuantSignal dataclass with all indicators, Fibonacci, and arbitrage data.
        """
        logger.info("quant_analysis_start", ticker=ticker)

        df = await self.fetch_price_data(ticker, period="1y", interval="1d")
        closes = df["Close"].squeeze()
        volumes = df["Volume"].squeeze()

        # Capture last bar for DB persistence
        ohlcv = {
            "open": float(df["Open"].iloc[-1]),
            "high": float(df["High"].iloc[-1]),
            "low": float(df["Low"].iloc[-1]),
            "close": float(df["Close"].iloc[-1]),
            "volume": int(df["Volume"].iloc[-1]),
        }

        signals = generate_signals(closes, volumes)

        # Override price with live quote (pre-market / regular / after-hours)
        try:
            live_price, prev_close = await self.fetch_live_price(ticker)
            signals["price"] = live_price
            if prev_close is not None:
                signals["prev_close"] = prev_close
            logger.debug("live_price_fetched", ticker=ticker, price=live_price)
        except Exception as exc:  # noqa: BLE001
            logger.warning("live_price_fallback", ticker=ticker, error=str(exc))

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

        result = QuantSignal(
            ticker=ticker,
            timestamp=now_utc().isoformat(),
            price=signals["price"],
            signals=signals,
            fibonacci=fib_dict,
            market_status=market_status(),
            ohlcv=ohlcv,
        )

        logger.info(
            "quant_analysis_complete",
            ticker=ticker,
            price=signals["price"],
            rsi=signals.get("rsi"),
        )
        return result

    async def analyze_timeframe(self, ticker: str, interval: str = "1wk") -> dict:
        """Fetch RSI + MACD direction for a single timeframe.

        Uses 1y of data for weekly, 5y for monthly to ensure enough bars.
        Results are cached under quote:{ticker}:{interval} with a 1-hour TTL.

        Args:
            ticker: Ticker symbol.
            interval: yfinance interval string: '1wk' or '1mo'.

        Returns:
            Dict with keys: interval, rsi, rsi_signal, macd_bullish.
            On failure returns a dict with interval key and all others None.
        """
        cache_key = f"quote:{ticker.upper()}:{interval}"
        cached = await cache.get(cache_key)
        if cached and "rsi" in cached:
            logger.debug("timeframe_cache_hit", ticker=ticker, interval=interval)
            return cached

        period = "5y" if interval == "1mo" else "2y"
        try:
            df = await self.fetch_price_data(ticker, period=period, interval=interval)
            closes = df["Close"].squeeze()
            volumes = df["Volume"].squeeze()
            sigs = generate_signals(closes, volumes)
            result = {
                "interval": interval,
                "rsi": sigs.get("rsi"),
                "rsi_signal": sigs.get("rsi_signal", "NEUTRAL"),
                "macd_bullish": (sigs.get("macd_histogram", 0) or 0) > 0,
            }
            await cache.set(cache_key, result, ttl=3600)
            return result
        except Exception as exc:
            logger.warning(
                "timeframe_fetch_failed",
                ticker=ticker,
                interval=interval,
                error=str(exc),
            )
            return {
                "interval": interval,
                "rsi": None,
                "rsi_signal": "NEUTRAL",
                "macd_bullish": None,
            }

    async def health_check(self) -> dict[str, str]:
        """Agent health check."""
        try:
            df = await self.fetch_price_data("SPY", period="5d", interval="1d")
            if df.empty:
                return {"status": "error", "detail": "yfinance returned empty data"}
            return {"status": "ok", "detail": f"yfinance OK — {len(df)} bars fetched"}
        except Exception as exc:
            return {"status": "error", "detail": str(exc)}

    async def run_loop(self) -> None:
        """Continuous analysis loop — polls watchlist every minute during market hours."""
        self._running = True
        logger.info("quant_engine_started")
        while self._running:
            status = market_status()
            if status["nyse_open"] or status["tase_open"]:
                await self._poll_watchlist()
            await asyncio.sleep(self._poll_interval)

    async def _poll_watchlist(self) -> None:
        """Analyze all watchlist tickers and cache results in Redis."""
        watchlist = list(_WATCHLIST_BASE)

        for ticker in watchlist:
            try:
                signal = await self.analyze(ticker)
                # Cache signal for 2 minutes
                await cache.set(f"signal:{ticker}", dataclasses.asdict(signal), ttl=120)
                await self._upsert_price_history(ticker, signal)
            except Exception as exc:
                logger.warning("watchlist_poll_failed", ticker=ticker, error=str(exc))

        logger.debug("watchlist_poll_complete", count=len(watchlist))

    async def _upsert_price_history(self, ticker: str, signal: QuantSignal) -> None:
        """Persist the latest OHLCV bar to PostgreSQL via SQLAlchemy ORM.

        Args:
            ticker: Ticker symbol.
            signal: QuantSignal containing ohlcv and timestamp.
        """
        if not signal.ohlcv:
            return

        exchange = "TASE" if ticker.upper().endswith(".TA") else "NYSE"
        timestamp = datetime.fromisoformat(signal.timestamp)

        async with AsyncSessionLocal() as session:
            stmt = select(PriceHistory).where(
                PriceHistory.ticker == ticker.upper(),
                PriceHistory.timestamp == timestamp,
                PriceHistory.timeframe == "1d",
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()

            if existing is not None:
                existing.close = signal.ohlcv["close"]
                existing.volume = signal.ohlcv["volume"]
            else:
                row = PriceHistory(
                    ticker=ticker.upper(),
                    exchange=exchange,
                    timestamp=timestamp,
                    timeframe="1d",
                    open=signal.ohlcv["open"],
                    high=signal.ohlcv["high"],
                    low=signal.ohlcv["low"],
                    close=signal.ohlcv["close"],
                    volume=signal.ohlcv["volume"],
                )
                session.add(row)

            await session.commit()

    def stop(self) -> None:
        """Stop the run loop."""
        self._running = False
        logger.info("quant_engine_stopped")
