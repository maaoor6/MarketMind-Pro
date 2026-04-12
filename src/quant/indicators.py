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


def generate_signals(prices: pd.Series, volume: pd.Series) -> dict:
    """Generate a comprehensive signal report for a price series.

    Returns a dict suitable for caching / Telegram dispatch.
    """
    mas = all_moving_averages(prices)
    rsi_result = rsi(prices)
    macd_result = macd(prices)
    vol_spikes = volume_spike(volume)

    latest = prices.iloc[-1]
    latest_rsi = (
        float(rsi_result.values.iloc[-1]) if not rsi_result.values.empty else None
    )

    return {
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
