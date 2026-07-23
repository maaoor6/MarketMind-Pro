"""Historical replay of the live dynamic-universe scanner.

The live :class:`~src.trading.universe.UniverseScanner` asks Yahoo's
predefined screeners for day gainers → most actives → day losers, filters
for quality, pins SPY + held positions, and caps the list. Historical
screener snapshots don't exist, so this module rebuilds each day's ranking
from the loaded candidate pool using that day's own bars and the same
filters and merge rules.

Documented limitation: the pool contains only tickers that exist today
(survivorship bias); the market-cap filter is approximated by restricting
the pool to large caps.
"""

import pandas as pd

from src.trading.stockarena_client import Portfolio
from src.trading.universe import UniverseScanner
from src.utils.config import settings

_PER_SCREENER = 25  # matches the live yf.screen(name, count=25)
_AVG_VOLUME_WINDOW = 63  # ~3 months of trading days


def daily_screen_columns(feats: pd.DataFrame) -> pd.DataFrame:
    """Add the per-bar columns the screener replay sorts and filters on."""
    out = feats
    out["pct_change_1d"] = out["Close"].pct_change() * 100
    out["avg_vol_3mo"] = (
        out["Volume"].rolling(_AVG_VOLUME_WINDOW, min_periods=20).mean()
    )
    return out


def _passes_filters(row: pd.Series, max_price: float | None) -> bool:
    """Mirror of the live ``_passes_filters`` on one day's bar."""
    price = row.get("Close")
    if price is None or pd.isna(price) or float(price) < settings.trading_min_price:
        return False
    if max_price is not None and float(price) > max_price:
        return False
    volume = row.get("avg_vol_3mo")
    if (
        volume is None
        or pd.isna(volume)
        or float(volume) < settings.trading_min_avg_volume
    ):
        return False
    # Quality gate (live parity): only long-term uptrends enter the universe.
    # Fail-open when SMA200 hasn't warmed up yet.
    sma200 = row.get("SMA_200")
    if sma200 is not None and not pd.isna(sma200) and float(price) < float(sma200):
        return False
    return True


def simulate_universe(
    day_rows: dict[str, pd.Series],
    portfolio: Portfolio | None = None,
) -> list[str]:
    """Return the tickers the live agent would have evaluated on this day.

    Args:
        day_rows: ticker → that day's feature row (only tickers trading today).
        portfolio: Current simulated portfolio (held positions get pinned).

    Returns:
        Ordered ticker list: SPY + held first, then screener candidates,
        deduped and capped at ``settings.trading_universe_size``.
    """
    held = sorted(portfolio.positions) if portfolio is not None else []
    pinned = ["SPY", *[t for t in held if t != "SPY"]]

    max_price = None
    if portfolio is not None and portfolio.total_value > 0:
        max_price = portfolio.total_value * settings.max_position_pct

    eligible = {
        ticker: row
        for ticker, row in day_rows.items()
        if not pd.isna(row.get("pct_change_1d")) and _passes_filters(row, max_price)
    }

    # Screener priority order mirrors the live _SCREENERS tuple:
    # gainers (momentum) then most actives (liquidity). day_losers was
    # removed (negative selection — see src/trading/universe.py).
    gainers = sorted(eligible, key=lambda t: eligible[t]["pct_change_1d"], reverse=True)
    actives = sorted(eligible, key=lambda t: eligible[t]["Volume"], reverse=True)

    candidates: list[str] = []
    seen: set[str] = set()
    for screener in (gainers, actives):
        for ticker in screener[:_PER_SCREENER]:
            if ticker not in seen:
                seen.add(ticker)
                candidates.append(ticker)

    # Stable core (live parity): reserve part of the universe for watchlist
    # names that are actually trading today.
    core = [t for t in settings.trading_watchlist if t in day_rows]
    return UniverseScanner.merge_with_core(pinned, candidates, core)
