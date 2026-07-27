"""Vectorized indicator precompute + per-bar StrategyContext assembly.

The live agent recomputes ``generate_signals`` on a growing slice every
cycle; doing that per bar over 30 years is O(bars²). All indicators used
here are causal (rolling / ewm), so computing them once on the full series
and reading the value at bar *t* is equivalent to computing on the slice
ending at *t* — this module does exactly that, reusing the same functions
from :mod:`src.quant.indicators`.

One documented approximation: live weekly/monthly MACD includes the
in-progress week/month; here only completed periods are used (except on
period-end days), which avoids lookahead and is slightly more conservative.
"""

import numpy as np
import pandas as pd

from src.quant.fibonacci import RETRACEMENT_LEVELS
from src.quant.indicators import (
    ADX_PERIOD,
    ATR_PERIOD,
    DONCHIAN_HIGH_PERIOD,
    DONCHIAN_LOW_PERIOD,
    KELTNER_ATR_MULT,
    RETURN_WINDOWS,
    SEVEN_DAY_WINDOW,
    SUPERTREND_MULT,
    SUPERTREND_PERIOD,
    TRADING_DAYS_PER_YEAR,
    VOLATILITY_WINDOW,
    adx,
    all_moving_averages,
    atr,
    bollinger,
    macd,
    momentum_score,
    rsi,
    sma,
    supertrend,
    volume_spike,
)
from src.trading.stockarena_client import Position
from src.trading.strategies import StrategyContext

# Bars skipped at the start of each ticker before signals are allowed —
# covers SMA_200 warmup plus EWM settling for RSI/MACD.
BURN_IN_BARS = 250

_FIB_WINDOW = 252  # matches calculate_fibonacci(window_days=252)
_TREND_LOOKBACK = 20  # matches calculate_fibonacci trend comparison


def precompute_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add every per-bar indicator column needed by ``build_context``.

    Args:
        df: Daily OHLCV frame from :func:`src.backtest.data.load_history`.

    Returns:
        Copy of ``df`` with indicator columns appended.
    """
    out = df.copy()
    closes = out["Close"]
    volumes = out["Volume"]

    out["rsi"] = rsi(closes).values
    macd_res = macd(closes)
    out["macd_line"] = macd_res.macd_line
    out["macd_signal"] = macd_res.signal_line
    out["macd_histogram"] = macd_res.histogram
    out["macd_histogram_prev"] = macd_res.histogram.shift(1)
    out["volume_spike"] = volume_spike(volumes)
    for name, series in all_moving_averages(closes).items():
        out[name] = series

    out["rsi_2"] = rsi(closes, period=2).values
    out["sma_5"] = sma(closes, 5)
    bb = bollinger(closes)
    out["bb_upper"] = bb.upper
    out["bb_middle"] = bb.middle
    out["bb_lower"] = bb.lower
    # Donchian channels end at the PREVIOUS bar (same rationale as the 52w
    # Fibonacci window below — today's close must be able to break them).
    prior_close = closes.shift(1)
    out["donchian_high_20"] = prior_close.rolling(
        DONCHIAN_HIGH_PERIOD, min_periods=DONCHIAN_HIGH_PERIOD
    ).max()
    out["donchian_low_10"] = prior_close.rolling(
        DONCHIAN_LOW_PERIOD, min_periods=DONCHIAN_LOW_PERIOD
    ).min()
    for name, window in RETURN_WINDOWS.items():
        out[name] = closes / closes.shift(window) - 1.0

    # 7-day close extremes INCLUDING today (Connors Double-7 semantics).
    out["low_7d_close"] = closes.rolling(
        SEVEN_DAY_WINDOW, min_periods=SEVEN_DAY_WINDOW
    ).min()
    out["high_7d_close"] = closes.rolling(
        SEVEN_DAY_WINDOW, min_periods=SEVEN_DAY_WINDOW
    ).max()
    # Previous bar's SMA50/SMA200 — fresh golden/death cross detection.
    out["sma_50_prev"] = out["SMA_50"].shift(1)
    out["sma_200_prev"] = out["SMA_200"].shift(1)
    # Annualized 20-bar realized volatility of daily returns.
    out["vol_20d"] = closes.pct_change().rolling(
        VOLATILITY_WINDOW, min_periods=VOLATILITY_WINDOW
    ).std() * np.sqrt(TRADING_DAYS_PER_YEAR)

    # ATR family (needs High/Low) — mirrors generate_signals when OHLC given.
    highs = out["High"]
    lows = out["Low"]
    atr_series = atr(highs, lows, closes, ATR_PERIOD)
    out["atr_14"] = atr_series
    out["atr_pct"] = atr_series / closes
    adx_res = adx(highs, lows, closes, ADX_PERIOD)
    out["adx_14"] = adx_res.adx
    out["plus_di"] = adx_res.plus_di
    out["minus_di"] = adx_res.minus_di
    out["supertrend_dir"] = supertrend(
        highs, lows, closes, SUPERTREND_PERIOD, SUPERTREND_MULT
    )
    out["keltner_upper"] = out["EMA_20"] + KELTNER_ATR_MULT * atr_series
    out["keltner_lower"] = out["EMA_20"] - KELTNER_ATR_MULT * atr_series

    # 52-week window for Fibonacci, ending at the PREVIOUS bar. Live, the
    # decision price is a fresh quote compared against levels computed from
    # already-closed daily bars — so today's close must be able to exceed
    # high_52w (that's what makes Breakout fire). Including today's close in
    # the window would make `price > resistance` structurally impossible.
    prior_closes = closes.shift(1)
    out["high_52w"] = prior_closes.rolling(_FIB_WINDOW, min_periods=1).max()
    out["low_52w"] = prior_closes.rolling(_FIB_WINDOW, min_periods=1).min()
    # Trend reference: price 20 bars back (iloc[-20] of the slice ending here).
    out["trend_ref"] = closes.shift(_TREND_LOOKBACK - 1)

    # Weekly / monthly MACD-bullish from locally resampled closes. Period
    # labels carry only data up to the label date, so a forward-fill onto the
    # daily index never looks ahead.
    for column, rule in (
        ("weekly_macd_bullish", "W-FRI"),
        ("monthly_macd_bullish", "ME"),
    ):
        period_close = closes.resample(rule).last().dropna()
        if len(period_close) >= 2:
            hist = macd(period_close).histogram > 0
            out[column] = hist.reindex(out.index, method="ffill")
        else:
            out[column] = None
    return out


def fib_snapshot(
    high_52w: float, low_52w: float, price: float, trend: str
) -> dict | None:
    """Flattened Fibonacci dict, mirroring ``calculate_fibonacci`` exactly.

    Returns None when high == low (live raises ValueError → context skipped).
    Support/resistance fall back to the 52w low/high, matching the live
    behavior where ``nearest_resistance`` is never None.
    """
    spread = high_52w - low_52w
    if spread == 0:
        return None
    retracements = [
        round(high_52w - spread * ratio, 4) for ratio in RETRACEMENT_LEVELS.values()
    ]
    support_levels = [v for v in retracements if v < price]
    resistance_levels = [v for v in retracements if v > price]
    return {
        "high_52w": high_52w,
        "low_52w": low_52w,
        "trend": trend,
        "nearest_support": max(support_levels) if support_levels else low_52w,
        "nearest_resistance": min(resistance_levels) if resistance_levels else high_52w,
    }


def build_context(
    ticker: str,
    feats: pd.DataFrame,
    i: int,
    position: Position | None = None,
    cross_section: dict | None = None,
) -> StrategyContext | None:
    """Assemble the StrategyContext for bar ``i``, as the live agent would.

    Fail-closed: returns None during burn-in or when core data is missing,
    matching the live ``_build_context`` → skip-ticker behavior.
    """
    if i < BURN_IN_BARS or i >= len(feats):
        return None
    row = feats.iloc[i]
    price = float(row["Close"])
    rsi_val = row["rsi"]
    if pd.isna(price) or pd.isna(rsi_val) or pd.isna(row["SMA_200"]):
        return None
    rsi_val = float(rsi_val)

    signals = {
        "price": price,
        "prev_close": float(feats["Close"].iloc[i - 1]),
        "rsi": rsi_val,
        "rsi_signal": (
            "OVERSOLD"
            if rsi_val <= 30
            else "OVERBOUGHT" if rsi_val >= 70 else "NEUTRAL"
        ),
        "macd_line": float(row["macd_line"]),
        "macd_signal": float(row["macd_signal"]),
        "macd_histogram": float(row["macd_histogram"]),
        "macd_histogram_prev": (
            None
            if pd.isna(row["macd_histogram_prev"])
            else float(row["macd_histogram_prev"])
        ),
        "volume_spike": bool(row["volume_spike"]),
        **{
            key: (None if pd.isna(row[key]) else float(row[key]))
            for key in (
                "rsi_2",
                "sma_5",
                "bb_upper",
                "bb_middle",
                "bb_lower",
                "donchian_high_20",
                "donchian_low_10",
                "low_7d_close",
                "high_7d_close",
                "sma_50_prev",
                "sma_200_prev",
                "vol_20d",
                "atr_14",
                "atr_pct",
                "adx_14",
                "plus_di",
                "minus_di",
                "supertrend_dir",
                "keltner_upper",
                "keltner_lower",
                *RETURN_WINDOWS,
            )
        },
        "moving_averages": {
            key: float(row[key])
            for key in (
                f"{kind}_{period}"
                for kind in ("SMA", "EMA")
                for period in (20, 50, 100, 150, 200)
            )
            if not pd.isna(row[key])
        },
    }

    trend_ref = row["trend_ref"]
    trend = (
        "UPTREND"
        if not pd.isna(trend_ref) and price > float(trend_ref)
        else "DOWNTREND"
    )
    fib = fib_snapshot(float(row["high_52w"]), float(row["low_52w"]), price, trend)
    if fib is None:
        return None

    def _timeframe(interval: str, column: str) -> dict:
        bullish = row[column]
        return {
            "interval": interval,
            "rsi": None,
            "rsi_signal": None,
            "macd_bullish": None if pd.isna(bullish) else bool(bullish),
        }

    # momentum_score only reads len>=6 and iloc[-6] from the price series —
    # a 6-bar tail slice keeps it O(1) and numerically identical.
    momentum = momentum_score(signals, feats["Close"].iloc[max(0, i - 5) : i + 1])

    bar_ts = feats.index[i]
    return StrategyContext(
        ticker=ticker,
        signals=signals,
        fibonacci=fib,
        weekly=_timeframe("1wk", "weekly_macd_bullish"),
        monthly=_timeframe("1mo", "monthly_macd_bullish"),
        momentum=momentum,
        position=position,
        as_of=bar_ts.date() if hasattr(bar_ts, "date") else None,
        cross_section=cross_section,
    )
