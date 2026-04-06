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
from src.quant.arbitrage import DUAL_LISTED, calculate_arbitrage
from src.quant.fibonacci import FibonacciLevels, calculate_fibonacci
from src.quant.indicators import generate_signals
from src.utils.logger import get_logger
from src.utils.timezone_utils import market_status, now_utc

logger = get_logger(__name__)

# Tickers polled every minute during market hours
_WATCHLIST_BASE = ["AAPL", "MSFT", "SPY", "QQQ"]


@dataclass
class QuantSignal:
    ticker: str
    timestamp: str
    price: float
    signals: dict
    fibonacci: dict | None = None
    arbitrage: dict | None = None
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
                tase_df = await self.fetch_price_data(
                    tase_ticker, period="5d", interval="1d"
                )
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
            ohlcv=ohlcv,
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
        # Deduplicated watchlist: dual-listed + base US stocks
        seen: set[str] = set()
        watchlist: list[str] = []
        for t in list(DUAL_LISTED.keys()) + _WATCHLIST_BASE:
            if t not in seen:
                seen.add(t)
                watchlist.append(t)

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
