"""Dynamic trading universe — the agent discovers its own candidates.

Each cycle scans Yahoo Finance predefined screeners (day gainers, most
actives), applies liquidity + uptrend-quality filters, and blends the
results with a stable watchlist core. Currently held positions and SPY
(needed for the regime filter) are always included. On any scan failure the
scanner falls back to ``settings.trading_watchlist`` (fail-closed).
"""

import asyncio
import re
from collections.abc import Callable

import yfinance as yf

from src.database.cache import cache
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

UNIVERSE_CACHE_KEY = "trading:universe"
_UNIVERSE_CACHE_TTL = 900  # 15 min — screener ranks move slowly

# Screeners scanned in priority order: momentum names first (gainers), then
# liquidity (most actives). day_losers was removed 2026-07-18: feeding the
# agent the day's crashers is negative selection — every promoted live
# strategy requires an uptrend, and backtests showed the dynamic universe
# was the main reason the combined agent lost while strategies won.
_SCREENERS = ("day_gainers", "most_actives")

# Plain US common-stock / ETF symbols only — no warrants, units, or indices.
_SYMBOL_RE = re.compile(r"^[A-Z]{1,5}$")

_ALLOWED_QUOTE_TYPES = {"EQUITY", "ETF"}


def _passes_filters(quote: dict, max_price: float | None) -> bool:
    """Liquidity / quality filters for one screener quote."""
    symbol = str(quote.get("symbol") or "")
    if not _SYMBOL_RE.match(symbol):
        return False
    quote_type = str(quote.get("quoteType") or "EQUITY").upper()
    if quote_type not in _ALLOWED_QUOTE_TYPES:
        return False
    price = quote.get("regularMarketPrice")
    if price is None or float(price) < settings.trading_min_price:
        return False
    if max_price is not None and float(price) > max_price:
        return False  # can't buy even one share within the position cap
    volume = quote.get("averageDailyVolume3Month")
    if volume is None or float(volume) < settings.trading_min_avg_volume:
        return False
    market_cap = quote.get("marketCap")
    if quote_type == "EQUITY" and (
        market_cap is None or float(market_cap) < settings.trading_min_market_cap
    ):
        return False
    # Quality gate: only names in a long-term uptrend (price above the
    # 200-day average) may enter the universe — the same filter every
    # promoted live strategy applies anyway. Fail-open when the screener
    # doesn't supply the average.
    sma200 = quote.get("twoHundredDayAverage")
    if sma200 is not None and float(sma200) > 0 and float(price) < float(sma200):
        return False
    return True


class UniverseScanner:
    """Builds the per-cycle candidate ticker list for the trading agent."""

    def __init__(self, screen_fn: Callable[..., dict] | None = None) -> None:
        self._screen = screen_fn or yf.screen

    async def get_universe(self, portfolio=None) -> list[str]:
        """Return the tickers to evaluate this cycle.

        Held positions and SPY always come first (exits and the regime
        filter must never be starved of data), then scanned candidates up
        to ``settings.trading_universe_size`` total.
        """
        held = sorted(portfolio.positions) if portfolio is not None else []
        pinned = ["SPY", *[t for t in held if t != "SPY"]]

        if not settings.trading_dynamic_universe:
            return self._merge(pinned, settings.trading_watchlist)

        candidates = await self._cached_scan(portfolio)
        if not candidates:
            logger.warning("universe_scan_empty_falling_back_to_watchlist")
            return self._merge(pinned, settings.trading_watchlist)
        return self.merge_with_core(pinned, candidates, settings.trading_watchlist)

    async def _cached_scan(self, portfolio) -> list[str]:
        try:
            cached = await cache.get(UNIVERSE_CACHE_KEY)
            if isinstance(cached, list) and cached:
                return [str(t).upper() for t in cached]
        except Exception as exc:  # noqa: BLE001
            logger.debug("universe_cache_read_failed", error=str(exc))

        candidates = await self._scan(portfolio)
        if candidates:
            try:
                await cache.set(UNIVERSE_CACHE_KEY, candidates, ttl=_UNIVERSE_CACHE_TTL)
            except Exception as exc:  # noqa: BLE001
                logger.debug("universe_cache_write_failed", error=str(exc))
        return candidates

    async def _scan(self, portfolio) -> list[str]:
        """Run the Yahoo screeners off-thread and filter the results."""
        max_price = None
        if portfolio is not None and portfolio.total_value > 0:
            max_price = portfolio.total_value * settings.max_position_pct

        results: list[str] = []
        seen: set[str] = set()
        for name in _SCREENERS:
            try:
                payload = await asyncio.to_thread(self._screen, name, count=25)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "universe_screener_failed", screener=name, error=str(exc)
                )
                continue
            for quote in payload.get("quotes") or []:
                if not isinstance(quote, dict):
                    continue
                symbol = str(quote.get("symbol") or "").upper()
                if symbol in seen or not _passes_filters(quote, max_price):
                    continue
                seen.add(symbol)
                results.append(symbol)

        logger.info("universe_scanned", candidates=len(results))
        return results

    @staticmethod
    def _merge(pinned: list[str], candidates: list[str]) -> list[str]:
        """Pinned tickers first, then candidates, deduped, capped at size."""
        universe: list[str] = []
        for ticker in [*pinned, *candidates]:
            ticker = ticker.upper()
            if ticker not in universe:
                universe.append(ticker)
            if len(universe) >= max(settings.trading_universe_size, len(pinned)):
                break
        return universe

    @staticmethod
    def merge_with_core(
        pinned: list[str], candidates: list[str], core: list[str]
    ) -> list[str]:
        """Merge with a stable core: pinned → core share → dynamic candidates.

        ``UNIVERSE_QUALITY_RATIO`` of the non-pinned slots is reserved for
        the stable watchlist, so the agent never trades ONLY the day's
        headlines. Shared by the live scanner and the backtest replay.
        """
        size = max(settings.trading_universe_size, len(pinned))
        free_slots = max(0, size - len({t.upper() for t in pinned}))
        core_slots = round(free_slots * settings.universe_quality_ratio)
        core_clean = [
            t for t in (c.upper() for c in core) if t not in {p.upper() for p in pinned}
        ]
        return UniverseScanner._merge(pinned, [*core_clean[:core_slots], *candidates])
