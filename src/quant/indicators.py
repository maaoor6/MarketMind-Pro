"""Technical analysis indicators: SMA, EMA, RSI, MACD."""

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class MomentumScore:
    """Composite momentum score derived from 5 technical indicators."""

    score: int  # 0–100
    label: str  # "Very Strong" | "Strong" | "Neutral" | "Weak" | "Very Weak"
    emoji: str  # 🔥 🟢 🟡 🟠 🔴
    breakdown: dict[str, int]  # component name → points awarded


@dataclass
class MACDResult:
    macd_line: pd.Series
    signal_line: pd.Series
    histogram: pd.Series


@dataclass
class BollingerResult:
    upper: pd.Series
    middle: pd.Series
    lower: pd.Series


@dataclass
class RSIResult:
    values: pd.Series
    overbought: float = 70.0
    oversold: float = 30.0

    def is_overbought(self) -> pd.Series:
        return self.values >= self.overbought

    def is_oversold(self) -> pd.Series:
        return self.values <= self.oversold


def sma(prices: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average.

    Args:
        prices: Series of closing prices.
        period: Lookback window in bars.

    Returns:
        SMA series aligned with input index.
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    return prices.rolling(window=period, min_periods=period).mean()


def ema(prices: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average.

    Args:
        prices: Series of closing prices.
        period: Span (equivalent to N-day EMA).

    Returns:
        EMA series aligned with input index.
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    return prices.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(prices: pd.Series, period: int = 14) -> RSIResult:
    """Relative Strength Index (Wilder's smoothing method).

    Args:
        prices: Series of closing prices.
        period: RSI lookback period (default 14).

    Returns:
        RSIResult with values Series.
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")

    delta = prices.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    # Wilder's smoothing (equivalent to EMA with alpha=1/period)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.finfo(float).eps)
    rsi_values = 100.0 - (100.0 / (1.0 + rs))
    rsi_values[avg_loss == 0] = 100.0
    rsi_values[avg_gain == 0] = 0.0

    return RSIResult(values=rsi_values)


def macd(
    prices: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> MACDResult:
    """MACD (Moving Average Convergence Divergence).

    Args:
        prices: Series of closing prices.
        fast: Fast EMA period (default 12).
        slow: Slow EMA period (default 26).
        signal: Signal line EMA period (default 9).

    Returns:
        MACDResult with macd_line, signal_line, histogram.
    """
    ema_fast = ema(prices, fast)
    ema_slow = ema(prices, slow)
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    histogram = macd_line - signal_line
    return MACDResult(
        macd_line=macd_line,
        signal_line=signal_line,
        histogram=histogram,
    )


def bollinger(
    prices: pd.Series,
    period: int = 20,
    num_std: float = 2.0,
) -> BollingerResult:
    """Bollinger Bands (SMA ± num_std × rolling standard deviation).

    Args:
        prices: Series of closing prices.
        period: Lookback window in bars (default 20).
        num_std: Band width in standard deviations (default 2.0).

    Returns:
        BollingerResult with upper, middle, lower band Series.
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    middle = sma(prices, period)
    std = prices.rolling(window=period, min_periods=period).std()
    return BollingerResult(
        upper=middle + num_std * std,
        middle=middle,
        lower=middle - num_std * std,
    )


def volume_spike(
    volume: pd.Series,
    ma_period: int = 10,
    spike_multiplier: float = 2.0,
) -> pd.Series:
    """Detect volume spikes vs N-day moving average.

    Args:
        volume: Series of daily volume values.
        ma_period: Moving average lookback (default 10-day per spec).
        spike_multiplier: Threshold multiplier (default 2x).

    Returns:
        Boolean Series — True where volume is a spike.
    """
    vol_ma = sma(volume, ma_period)
    return volume >= (vol_ma * spike_multiplier)


@dataclass
class ADXResult:
    adx: pd.Series
    plus_di: pd.Series
    minus_di: pd.Series


def atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Average True Range (Wilder's smoothing).

    True Range = max(high-low, |high-prev_close|, |low-prev_close|).
    Wilder's ATR is the RMA (EMA with alpha=1/period) of the True Range.

    Args:
        high/low/close: Aligned OHLC series.
        period: Lookback (default 14).

    Returns:
        ATR series aligned with the input index.
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> ADXResult:
    """Average Directional Index + directional indicators (Wilder).

    ADX measures trend STRENGTH (not direction); +DI/−DI give direction.
    A common rule: ADX > 25 = strong trend, and +DI > −DI = up.

    Args:
        high/low/close: Aligned OHLC series.
        period: Lookback (default 14).

    Returns:
        ADXResult with adx, plus_di, minus_di series.
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    up = high.diff()
    down = -low.diff()
    plus_dm = ((up > down) & (up > 0)) * up.clip(lower=0)
    minus_dm = ((down > up) & (down > 0)) * down.clip(lower=0)

    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)

    def _rma(s: pd.Series) -> pd.Series:
        return s.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    atr_ = _rma(tr).replace(0, np.finfo(float).eps)
    plus_di = 100 * _rma(plus_dm) / atr_
    minus_di = 100 * _rma(minus_dm) / atr_
    di_sum = (plus_di + minus_di).replace(0, np.finfo(float).eps)
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    adx_series = _rma(dx)
    return ADXResult(adx=adx_series, plus_di=plus_di, minus_di=minus_di)


def supertrend(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 10,
    multiplier: float = 3.0,
) -> pd.Series:
    """Supertrend direction (+1 uptrend / −1 downtrend), ATR-based.

    Causal: the value at bar *t* depends only on bars ≤ *t* (the band
    carry-forward rule), so computing on the full series and reading bar *t*
    equals computing on the slice ending at *t*.

    Returns:
        Integer Series of +1 / −1 (NaN during ATR warmup).
    """
    atr_ = atr(high, low, close, period)
    hl2 = (high + low) / 2
    upper = hl2 + multiplier * atr_
    lower = hl2 - multiplier * atr_

    n = len(close)
    direction = np.full(n, np.nan)
    final_upper = upper.to_numpy().copy()
    final_lower = lower.to_numpy().copy()
    c = close.to_numpy()
    atr_arr = atr_.to_numpy()

    dir_prev = 1
    started = False
    for i in range(n):
        if np.isnan(atr_arr[i]):
            continue
        if not started:
            dir_prev = 1
            started = True
            direction[i] = dir_prev
            continue
        # Carry-forward the tighter band while the trend holds.
        if c[i - 1] <= final_upper[i - 1]:
            final_upper[i] = min(final_upper[i], final_upper[i - 1])
        if c[i - 1] >= final_lower[i - 1]:
            final_lower[i] = max(final_lower[i], final_lower[i - 1])
        if c[i] > final_upper[i - 1]:
            dir_prev = 1
        elif c[i] < final_lower[i - 1]:
            dir_prev = -1
        direction[i] = dir_prev

    return pd.Series(direction, index=close.index)


def all_moving_averages(prices: pd.Series) -> dict[str, pd.Series]:
    """Compute SMA and EMA for periods 20, 50, 100, 150, 200.

    Returns:
        Dict with keys like 'SMA_20', 'EMA_50', etc.
    """
    periods = [20, 50, 100, 150, 200]
    result: dict[str, pd.Series] = {}
    for p in periods:
        result[f"SMA_{p}"] = sma(prices, p)
        result[f"EMA_{p}"] = ema(prices, p)
    return result


# Lookbacks (in trading bars) for the N-month return fields in
# generate_signals — 21 bars ≈ 1 month.
RETURN_WINDOWS: dict[str, int] = {
    "ret_1m": 21,
    "ret_3m": 63,
    "ret_6m": 126,
    "ret_12m": 252,
}

DONCHIAN_HIGH_PERIOD = 20
DONCHIAN_LOW_PERIOD = 10

# Windows for the Connors Double-7 extremes and the realized-volatility field.
SEVEN_DAY_WINDOW = 7
VOLATILITY_WINDOW = 20
TRADING_DAYS_PER_YEAR = 252

# ATR-family fields (need High/Low; computed only when they are supplied).
ATR_PERIOD = 14
ADX_PERIOD = 14
SUPERTREND_PERIOD = 10
SUPERTREND_MULT = 3.0
KELTNER_PERIOD = 20
KELTNER_ATR_MULT = 2.0


def _trailing_return(prices: pd.Series, window: int) -> float | None:
    """Return over the last ``window`` bars, or None without enough history."""
    if len(prices) <= window:
        return None
    base = float(prices.iloc[-window - 1])
    return (float(prices.iloc[-1]) - base) / base if base else None


def generate_signals(
    prices: pd.Series,
    volume: pd.Series,
    high: pd.Series | None = None,
    low: pd.Series | None = None,
) -> dict:
    """Generate a comprehensive signal report for a price series.

    Returns a dict suitable for caching / Telegram dispatch. When ``high`` and
    ``low`` are supplied, the ATR-family fields (atr_14, atr_pct, adx_14,
    plus_di, minus_di, supertrend_dir, keltner_upper/lower) are populated;
    otherwise they are None (callers that only pass close/volume don't use the
    ATR strategies).
    """
    mas = all_moving_averages(prices)
    rsi_result = rsi(prices)
    rsi2_result = rsi(prices, period=2)
    macd_result = macd(prices)
    vol_spikes = volume_spike(volume)
    bb = bollinger(prices)
    sma5 = sma(prices, 5)

    latest = prices.iloc[-1]
    latest_rsi = (
        float(rsi_result.values.iloc[-1]) if not rsi_result.values.empty else None
    )

    def _last(series: pd.Series) -> float | None:
        if series.empty or pd.isna(series.iloc[-1]):
            return None
        return float(series.iloc[-1])

    # Donchian channels over the window ENDING AT THE PREVIOUS BAR — like the
    # 52w Fibonacci window, today's price must be able to break the channel.
    prior = prices.iloc[:-1]
    donchian_high = (
        float(prior.tail(DONCHIAN_HIGH_PERIOD).max())
        if len(prior) >= DONCHIAN_HIGH_PERIOD
        else None
    )
    donchian_low = (
        float(prior.tail(DONCHIAN_LOW_PERIOD).min())
        if len(prior) >= DONCHIAN_LOW_PERIOD
        else None
    )

    # Rolling 7-day close extremes (INCLUDING today — Connors Double-7 asks
    # "is today's close the lowest/highest of the last 7?").
    low_7d = (
        float(prices.tail(SEVEN_DAY_WINDOW).min())
        if len(prices) >= SEVEN_DAY_WINDOW
        else None
    )
    high_7d = (
        float(prices.tail(SEVEN_DAY_WINDOW).max())
        if len(prices) >= SEVEN_DAY_WINDOW
        else None
    )

    # Previous bar's SMA50/SMA200 — lets strategies detect a FRESH cross,
    # same idea as macd_histogram_prev.
    sma50_series = mas["SMA_50"]
    sma200_series = mas["SMA_200"]
    sma50_prev = (
        float(sma50_series.iloc[-2])
        if len(sma50_series) >= 2 and not pd.isna(sma50_series.iloc[-2])
        else None
    )
    sma200_prev = (
        float(sma200_series.iloc[-2])
        if len(sma200_series) >= 2 and not pd.isna(sma200_series.iloc[-2])
        else None
    )

    # Annualized realized volatility of daily returns over the last 20 bars.
    vol_series = (
        prices.pct_change()
        .rolling(VOLATILITY_WINDOW, min_periods=VOLATILITY_WINDOW)
        .std()
    )
    vol_20d = (
        float(vol_series.iloc[-1] * np.sqrt(TRADING_DAYS_PER_YEAR))
        if not vol_series.empty and not pd.isna(vol_series.iloc[-1])
        else None
    )

    # ATR family — only when High/Low are available.
    atr_fields: dict[str, float | None] = {
        "atr_14": None,
        "atr_pct": None,
        "adx_14": None,
        "plus_di": None,
        "minus_di": None,
        "supertrend_dir": None,
        "keltner_upper": None,
        "keltner_lower": None,
    }
    if high is not None and low is not None:
        atr_series = atr(high, low, prices, ATR_PERIOD)
        atr_val = _last(atr_series)
        adx_res = adx(high, low, prices, ADX_PERIOD)
        st_dir = _last(
            supertrend(high, low, prices, SUPERTREND_PERIOD, SUPERTREND_MULT)
        )
        ema20 = _last(mas["EMA_20"])
        atr_fields = {
            "atr_14": atr_val,
            "atr_pct": (
                atr_val / float(latest) if atr_val is not None and latest else None
            ),
            "adx_14": _last(adx_res.adx),
            "plus_di": _last(adx_res.plus_di),
            "minus_di": _last(adx_res.minus_di),
            "supertrend_dir": st_dir,
            "keltner_upper": (
                ema20 + KELTNER_ATR_MULT * atr_val
                if ema20 is not None and atr_val is not None
                else None
            ),
            "keltner_lower": (
                ema20 - KELTNER_ATR_MULT * atr_val
                if ema20 is not None and atr_val is not None
                else None
            ),
        }

    return {
        "rsi_2": _last(rsi2_result.values),
        **atr_fields,
        "sma_5": _last(sma5),
        "low_7d_close": low_7d,
        "high_7d_close": high_7d,
        "sma_50_prev": sma50_prev,
        "sma_200_prev": sma200_prev,
        "vol_20d": vol_20d,
        "bb_upper": _last(bb.upper),
        "bb_middle": _last(bb.middle),
        "bb_lower": _last(bb.lower),
        "donchian_high_20": donchian_high,
        "donchian_low_10": donchian_low,
        **{
            name: _trailing_return(prices, window)
            for name, window in RETURN_WINDOWS.items()
        },
        "price": float(latest),
        "rsi": latest_rsi,
        "rsi_signal": (
            "OVERSOLD"
            if latest_rsi and latest_rsi <= 30
            else "OVERBOUGHT" if latest_rsi and latest_rsi >= 70 else "NEUTRAL"
        ),
        "macd_line": float(macd_result.macd_line.iloc[-1]),
        "macd_signal": float(macd_result.signal_line.iloc[-1]),
        "macd_histogram": float(macd_result.histogram.iloc[-1]),
        # Previous bar's histogram — lets strategies detect a FRESH cross.
        "macd_histogram_prev": (
            float(macd_result.histogram.iloc[-2]) if len(prices) >= 2 else None
        ),
        "volume_spike": bool(vol_spikes.iloc[-1]) if not vol_spikes.empty else False,
        "moving_averages": {
            k: float(v.iloc[-1]) for k, v in mas.items() if not v.empty
        },
    }


def momentum_score(signals: dict, prices: pd.Series) -> MomentumScore:
    """Compute a composite momentum score (0–100) from a generate_signals() dict.

    Components (20 points each):
      - RSI:        >70→20, 50–70→15, 40–50→10, <40→0
      - SMA200:     price > SMA200 → 20, else 0
      - MACD:       histogram > 0 → 20, else 0
      - VolSpike:   volume_spike=True → 20, else 0
      - 5d%Change:  >+3%→20, >+1%→15, >0→10, ≤0→0

    Args:
        signals: Dict returned by generate_signals().
        prices:  Closing price series (at least 6 bars required for 5d change).

    Returns:
        MomentumScore dataclass.
    """
    breakdown: dict[str, int] = {}

    # RSI component
    rsi_val = signals.get("rsi")
    if rsi_val is None:
        rsi_pts = 0
    elif rsi_val > 70:
        rsi_pts = 20
    elif rsi_val >= 50:
        rsi_pts = 15
    elif rsi_val >= 40:
        rsi_pts = 10
    else:
        rsi_pts = 0
    breakdown["rsi"] = rsi_pts

    # SMA200 component
    price = signals.get("price", 0.0)
    sma200 = signals.get("moving_averages", {}).get("SMA_200")
    sma200_pts = 20 if (sma200 is not None and price > sma200) else 0
    breakdown["sma200"] = sma200_pts

    # MACD component
    macd_hist = signals.get("macd_histogram", 0) or 0
    macd_pts = 20 if macd_hist > 0 else 0
    breakdown["macd"] = macd_pts

    # Volume spike component
    vol_pts = 20 if signals.get("volume_spike", False) else 0
    breakdown["volume"] = vol_pts

    # 5-day % change component
    if len(prices) >= 6:
        price_5d_ago = float(prices.iloc[-6])
        pct_5d = (price - price_5d_ago) / price_5d_ago * 100 if price_5d_ago else 0.0
    else:
        pct_5d = 0.0

    if pct_5d > 3.0:
        pct_pts = 20
    elif pct_5d > 1.0:
        pct_pts = 15
    elif pct_5d > 0:
        pct_pts = 10
    else:
        pct_pts = 0
    breakdown["5d_change"] = pct_pts

    total = sum(breakdown.values())

    if total >= 80:
        label, emoji = "Very Strong", "🔥"
    elif total >= 60:
        label, emoji = "Strong", "🟢"
    elif total >= 40:
        label, emoji = "Neutral", "🟡"
    elif total >= 20:
        label, emoji = "Weak", "🟠"
    else:
        label, emoji = "Very Weak", "🔴"

    return MomentumScore(score=total, label=label, emoji=emoji, breakdown=breakdown)
