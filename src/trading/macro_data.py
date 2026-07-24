"""Macro / market-timing data provider — strictly zero paid-API cost.

Gathers the quantitative inputs the :class:`~src.trading.macro_gate.MacroTimingGate`
reasons over: equity trend (SPY/QQQ vs SMA50/200), volatility (VIX + VIX9D term
structure), credit & rates (yield curve, MOVE, HYG/LQD), Fed net liquidity, and
cross-asset momentum (dollar / gold / oil). Prices come from the shared
:class:`~src.agents.quant_engine.QuantEngine` (yfinance, free); rates/liquidity
come from FRED (free API key). Every field fails open to ``None`` so a single
unreachable source never blocks trading — the gate treats missing inputs as
neutral.

The provider only fetches; turning readings into an ON/OFF decision lives in
``macro_gate.py`` (pure, so the backtest can reuse it over historical readings).
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import time as dt_time

import httpx
import pandas as pd

from src.database.cache import cache
from src.quant.indicators import sma
from src.utils.config import settings
from src.utils.logger import get_logger
from src.utils.timezone_utils import now_us

logger = get_logger(__name__)

_CACHE_KEY = "macro:data"
_CACHE_TTL = 900  # 15 min — macro state moves slowly

_FRED_URL = "https://api.stlouisfed.org/fred/series/observations"

# A daily bar dated *today* before this ET time is still forming (partial) — a
# pre-market snapshot skewed the SMA flags in a live smoke test, so we drop it.
_NYSE_CLOSE = dt_time(16, 0)


@dataclass
class MacroData:
    """Raw macro readings at one point in time (all optional / fail-open)."""

    spy_above_sma50: bool | None = None
    spy_above_sma200: bool | None = None
    qqq_above_sma200: bool | None = None
    vix: float | None = None
    # VIX9D / VIX — > 1 = backwardation (near-term stress), < 1 = calm contango.
    vix_term_ratio: float | None = None
    # 10y − 2y Treasury spread (percentage points); negative = inverted curve.
    yield_curve: float | None = None
    move: float | None = None  # ICE BofA MOVE index (bond-market volatility)
    # HYG/LQD ratio 1-month momentum — rising = credit risk-on.
    hyg_lqd_trend: float | None = None
    # Fed net liquidity (WALCL − RRP − TGA) 1-month change, $bn.
    net_liquidity_trend: float | None = None
    dxy_trend: float | None = None  # dollar 3-month momentum
    gold_trend: float | None = None  # gold 3-month momentum
    oil_trend: float | None = None  # oil 3-month momentum

    def to_dict(self) -> dict:
        return asdict(self)


def _clean_closes(df: pd.DataFrame) -> pd.Series:
    """Return the Close series with NaNs and any partial current-day bar removed.

    yfinance's daily download appends a still-forming bar for the current
    session (and can return a transient malformed frame pre-market). Trusting
    its last value flipped borderline SMA flags in a live smoke test, so we
    drop a today-dated bar until the US session has actually closed.
    """
    closes = df["Close"].squeeze().dropna()
    if closes.empty:
        return closes
    try:
        last_date = closes.index[-1].date()
        now = now_us()
        if last_date == now.date() and now.time() < _NYSE_CLOSE:
            closes = closes.iloc[:-1]
    except (AttributeError, IndexError, TypeError):
        # Non-datetime index or empty after slicing — return what we have.
        pass
    return closes


def _sma_flag(closes: pd.Series, period: int) -> bool | None:
    """Is the last close above its ``period``-bar SMA? None if data insufficient.

    Returning None (not False) on a short/empty series keeps the gate fail-open:
    ``price > NaN`` silently evaluates False and would fake a bearish reading.
    """
    if closes is None or len(closes) < period:
        return None
    ma = sma(closes, period).iloc[-1]
    last = closes.iloc[-1]
    if pd.isna(ma) or pd.isna(last):
        return None
    return bool(float(last) > float(ma))


def _pct_change(series: pd.Series, window: int) -> float | None:
    """Trailing return over ``window`` bars, or None if too short."""
    s = series.dropna()
    if len(s) <= window:
        return None
    prev = float(s.iloc[-window - 1])
    if prev == 0:
        return None
    return float(s.iloc[-1]) / prev - 1.0


class MacroDataProvider:
    """Fetches macro readings, reusing the shared QuantEngine data plane."""

    def __init__(self, quant) -> None:
        self._quant = quant

    async def fetch(self, *, use_cache: bool = True) -> MacroData:
        """Assemble a :class:`MacroData`, Redis-cached for 15 minutes."""
        if use_cache:
            cached = await cache.get(_CACHE_KEY)
            if cached:
                return MacroData(**cached)

        equity, vix, credit, cross = await asyncio.gather(
            self._equity_trend(),
            self._volatility(),
            self._credit_rates(),
            self._cross_asset(),
            return_exceptions=True,
        )
        data = MacroData()
        for part in (equity, vix, credit, cross):
            if isinstance(part, dict):
                for key, value in part.items():
                    setattr(data, key, value)
            elif isinstance(part, Exception):
                logger.debug("macro_part_failed", error=str(part))

        await cache.set(_CACHE_KEY, data.to_dict(), ttl=_CACHE_TTL)
        return data

    # ── Sub-fetchers (each fail-open) ─────────────────────────────────

    async def _trend(self, ticker: str, window: int) -> float | None:
        try:
            df = await self._quant.fetch_price_data(ticker, period="1y", interval="1d")
            return _pct_change(_clean_closes(df), window)
        except Exception as exc:  # noqa: BLE001
            logger.debug("macro_trend_failed", ticker=ticker, error=str(exc))
            return None

    async def _equity_trend(self) -> dict:
        out: dict = {}
        try:
            spy = await self._quant.fetch_price_data("SPY", period="2y", interval="1d")
            closes = _clean_closes(spy)
            # Only publish a flag when it is trustworthy; None ⇒ neutral in the
            # score, never a fake bearish False from insufficient/partial data.
            for key, flag in (
                ("spy_above_sma50", _sma_flag(closes, 50)),
                ("spy_above_sma200", _sma_flag(closes, 200)),
            ):
                if flag is not None:
                    out[key] = flag
        except Exception as exc:  # noqa: BLE001
            logger.debug("macro_spy_failed", error=str(exc))
        try:
            qqq = await self._quant.fetch_price_data("QQQ", period="2y", interval="1d")
            flag = _sma_flag(_clean_closes(qqq), 200)
            if flag is not None:
                out["qqq_above_sma200"] = flag
        except Exception as exc:  # noqa: BLE001
            logger.debug("macro_qqq_failed", error=str(exc))
        return out

    async def _volatility(self) -> dict:
        out: dict = {}
        try:
            vix, _ = await self._quant.fetch_live_price("^VIX")
            out["vix"] = float(vix)
            vix9d, _ = await self._quant.fetch_live_price("^VIX9D")
            if vix and float(vix) > 0:
                out["vix_term_ratio"] = float(vix9d) / float(vix)
        except Exception as exc:  # noqa: BLE001
            logger.debug("macro_vix_failed", error=str(exc))
        return out

    async def _credit_rates(self) -> dict:
        out: dict = {}
        dgs10 = await self._fred_latest("DGS10")
        dgs2 = await self._fred_latest("DGS2")
        if dgs10 is not None and dgs2 is not None:
            out["yield_curve"] = dgs10 - dgs2

        # Fed net liquidity = balance sheet − reverse repo − Treasury account.
        walcl = await self._fred_series("WALCL", limit=30)
        rrp = await self._fred_series("RRPONTSYD", limit=30)
        tga = await self._fred_series("WTREGEN", limit=30)
        net_trend = _net_liquidity_trend(walcl, rrp, tga)
        if net_trend is not None:
            out["net_liquidity_trend"] = net_trend

        try:
            move, _ = await self._quant.fetch_live_price("^MOVE")
            out["move"] = float(move)
        except Exception as exc:  # noqa: BLE001
            logger.debug("macro_move_failed", error=str(exc))

        # Credit risk appetite: HYG (high-yield) vs LQD (investment-grade).
        try:
            hyg = await self._quant.fetch_price_data("HYG", period="6mo", interval="1d")
            lqd = await self._quant.fetch_price_data("LQD", period="6mo", interval="1d")
            ratio = (_clean_closes(hyg) / _clean_closes(lqd)).dropna()
            out["hyg_lqd_trend"] = _pct_change(ratio, 21)
        except Exception as exc:  # noqa: BLE001
            logger.debug("macro_credit_failed", error=str(exc))
        return out

    async def _cross_asset(self) -> dict:
        return {
            "dxy_trend": await self._trend("DX-Y.NYB", 63),
            "gold_trend": await self._trend("GLD", 63),
            "oil_trend": await self._trend("USO", 63),
        }

    # ── FRED (free) ───────────────────────────────────────────────────

    async def _fred_series(self, series_id: str, limit: int = 1) -> list[float] | None:
        """Latest ``limit`` numeric observations (newest first), or None."""
        if not settings.fred_api_key:
            return None
        params = {
            "series_id": series_id,
            "api_key": settings.fred_api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": limit,
        }
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(_FRED_URL, params=params)
                resp.raise_for_status()
                obs = resp.json().get("observations", [])
        except Exception as exc:  # noqa: BLE001
            logger.debug("fred_fetch_failed", series=series_id, error=str(exc))
            return None
        values: list[float] = []
        for o in obs:
            raw = o.get("value")
            try:
                values.append(float(raw))
            except (TypeError, ValueError):
                continue  # FRED uses "." for missing observations
        return values or None

    async def _fred_latest(self, series_id: str) -> float | None:
        values = await self._fred_series(series_id, limit=1)
        return values[0] if values else None


def _net_liquidity_trend(
    walcl: list[float] | None,
    rrp: list[float] | None,
    tga: list[float] | None,
) -> float | None:
    """1-month change in Fed net liquidity ($bn), newest-first inputs.

    Net liquidity = balance sheet − reverse repo − Treasury general account;
    a rising value is a tailwind for risk assets. Returns None if any leg is
    missing or too short.
    """
    if not walcl or not rrp or not tga:
        return None
    if min(len(walcl), len(rrp), len(tga)) < 5:
        return None

    def _net(i: int) -> float:
        # FRED units: WALCL & WTREGEN in $mn, RRPONTSYD in $bn → normalize to $bn.
        return walcl[i] / 1000.0 - rrp[i] - tga[i] / 1000.0

    latest = _net(0)
    # ~4 weekly observations back ≈ 1 month.
    prior_idx = min(4, len(walcl) - 1, len(rrp) - 1, len(tga) - 1)
    return latest - _net(prior_idx)
