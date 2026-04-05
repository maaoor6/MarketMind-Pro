"""Automated Fibonacci retracement and extension levels."""

from dataclasses import dataclass, field

import pandas as pd


# Standard Fibonacci retracement ratios
RETRACEMENT_LEVELS = {
    "0.0%": 0.0,
    "23.6%": 0.236,
    "38.2%": 0.382,
    "50.0%": 0.500,
    "61.8%": 0.618,
    "78.6%": 0.786,
    "100.0%": 1.0,
}

# Extension levels
EXTENSION_LEVELS = {
    "127.2%": 1.272,
    "161.8%": 1.618,
    "261.8%": 2.618,
}


@dataclass
class FibonacciLevels:
    """Fibonacci retracement levels computed from 52-week High/Low."""

    ticker: str
    high_52w: float
    low_52w: float
    current_price: float
    retracements: dict[str, float] = field(default_factory=dict)
    extensions: dict[str, float] = field(default_factory=dict)
    trend: str = "UNKNOWN"  # UPTREND or DOWNTREND
    nearest_support: float | None = None
    nearest_resistance: float | None = None

    def price_position(self) -> str:
        """Return where current price sits relative to Fibonacci levels."""
        pct_from_low = (self.current_price - self.low_52w) / (self.high_52w - self.low_52w)
        return f"{pct_from_low * 100:.1f}% from 52w low"


def calculate_fibonacci(
    prices: pd.Series,
    ticker: str = "UNKNOWN",
    window_days: int = 252,
) -> FibonacciLevels:
    """Compute Fibonacci retracement levels from 52-week High/Low.

    Args:
        prices: Daily closing price series (timezone-aware index preferred).
        ticker: Ticker symbol for labeling.
        window_days: Lookback window in trading days (default 252 ≈ 1 year).

    Returns:
        FibonacciLevels dataclass with all computed levels.
    """
    if len(prices) < 2:
        raise ValueError("Need at least 2 price points for Fibonacci calculation")

    window = prices.iloc[-window_days:] if len(prices) >= window_days else prices
    high_52w = float(window.max())
    low_52w = float(window.min())
    current_price = float(prices.iloc[-1])
    spread = high_52w - low_52w

    if spread == 0:
        raise ValueError(f"No price range in data for {ticker} (high == low)")

    # Retracements from high to low (price falling scenario)
    retracements: dict[str, float] = {}
    for label, ratio in RETRACEMENT_LEVELS.items():
        retracements[label] = round(high_52w - (spread * ratio), 4)

    # Extensions from low, projecting above high
    extensions: dict[str, float] = {}
    for label, ratio in EXTENSION_LEVELS.items():
        extensions[label] = round(low_52w + (spread * ratio), 4)

    # Determine trend: look at last 20 days vs 50 days
    trend = "UPTREND" if prices.iloc[-1] > prices.iloc[-min(20, len(prices))] else "DOWNTREND"

    # Nearest support (largest retracement level below current price)
    support_levels = [v for v in retracements.values() if v < current_price]
    resistance_levels = [v for v in retracements.values() if v > current_price]
    nearest_support = max(support_levels) if support_levels else low_52w
    nearest_resistance = min(resistance_levels) if resistance_levels else high_52w

    return FibonacciLevels(
        ticker=ticker,
        high_52w=high_52w,
        low_52w=low_52w,
        current_price=current_price,
        retracements=retracements,
        extensions=extensions,
        trend=trend,
        nearest_support=nearest_support,
        nearest_resistance=nearest_resistance,
    )


def format_fibonacci_message(levels: FibonacciLevels) -> str:
    """Format Fibonacci levels as a Telegram-friendly string."""
    lines = [
        f"📐 *Fibonacci Levels — {levels.ticker}*",
        f"Trend: {'📈' if levels.trend == 'UPTREND' else '📉'} {levels.trend}",
        f"52W High: ${levels.high_52w:,.2f} | 52W Low: ${levels.low_52w:,.2f}",
        f"Current: ${levels.current_price:,.2f} ({levels.price_position()})",
        "",
        "*Retracements:*",
    ]
    for label, price in levels.retracements.items():
        marker = "◀️" if abs(price - levels.current_price) < abs(
            (levels.nearest_support or 0) - levels.current_price
        ) else "  "
        lines.append(f"  {label}: ${price:,.2f} {marker}")

    lines += [
        "",
        "*Key Levels:*",
        f"  🟢 Support: ${levels.nearest_support:,.2f}" if levels.nearest_support else "",
        f"  🔴 Resistance: ${levels.nearest_resistance:,.2f}" if levels.nearest_resistance else "",
    ]
    return "\n".join(filter(None, lines))
