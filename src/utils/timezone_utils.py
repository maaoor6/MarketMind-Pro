"""Timezone-aware utilities for TASE and US market hours."""

from datetime import datetime, time

import pytz

from src.utils.config import settings

TZ_US = pytz.timezone(settings.timezone_us)
TZ_TASE = pytz.timezone(settings.timezone_tase)
TZ_UTC = pytz.UTC

# ── TASE trading hours (Israel time): Mon–Fri ─────────────────────────────────
# Monday–Thursday: 10:00–17:25 (pre-open starts 09:45)
# Friday:          10:00–15:45 (early close)
# Saturday/Sunday: closed
TASE_OPEN_REGULAR = time(10, 0)
TASE_CLOSE_REGULAR = time(17, 25)
TASE_CLOSE_FRIDAY = time(15, 45)
TASE_PREOPEN_TIME = time(9, 45)
TASE_TRADING_DAYS = {0, 1, 2, 3, 4}  # Mon=0, Tue=1, Wed=2, Thu=3, Fri=4
TASE_SHORT_DAY = 4  # Friday — early close

# ── NYSE/NASDAQ trading hours (ET): Mon–Fri 09:30–16:00 ──────────────────────
NYSE_OPEN = time(9, 30)
NYSE_CLOSE = time(16, 0)
NYSE_TRADING_DAYS = {0, 1, 2, 3, 4}  # Mon–Fri


def now_utc() -> datetime:
    """Current UTC datetime."""
    return datetime.now(TZ_UTC)


def now_us() -> datetime:
    """Current US Eastern datetime."""
    return datetime.now(TZ_US)


def now_tase() -> datetime:
    """Current Israel datetime."""
    return datetime.now(TZ_TASE)


def is_nyse_open() -> bool:
    """Return True if NYSE is currently in regular trading hours (Mon–Fri 09:30–16:00 ET)."""
    now = now_us()
    if now.weekday() not in NYSE_TRADING_DAYS:
        return False
    return NYSE_OPEN <= now.time() <= NYSE_CLOSE


def is_tase_open() -> bool:
    """Return True if TASE is currently in regular trading hours.

    TASE trades Mon–Fri in Israel local time:
      Mon–Thu: 10:00–17:25
      Friday:  10:00–15:45 (early close)
    """
    now = now_tase()
    weekday = now.weekday()
    if weekday not in TASE_TRADING_DAYS:
        return False
    t = now.time()
    if weekday == TASE_SHORT_DAY:
        return TASE_OPEN_REGULAR <= t <= TASE_CLOSE_FRIDAY
    return TASE_OPEN_REGULAR <= t <= TASE_CLOSE_REGULAR


def is_tase_preopen() -> bool:
    """Return True if TASE is in the pre-open window (09:45–10:00 IL, Mon–Fri)."""
    now = now_tase()
    if now.weekday() not in TASE_TRADING_DAYS:
        return False
    t = now.time()
    return TASE_PREOPEN_TIME <= t < TASE_OPEN_REGULAR


def is_friday_session() -> bool:
    """Return True if the current Israel day is Friday (early-close day)."""
    return now_tase().weekday() == TASE_SHORT_DAY


def currency_symbol(ticker: str) -> str:
    """Return the appropriate currency symbol for a ticker.

    Args:
        ticker: Ticker symbol (e.g., 'TEVA', 'TEVA.TA', 'AAPL').

    Returns:
        '₪' for TASE-listed tickers (ending in '.TA'), '$' otherwise.
    """
    return "₪" if ticker.upper().endswith(".TA") else "$"


def to_us_time(dt: datetime) -> datetime:
    """Convert any timezone-aware datetime to US Eastern."""
    if dt.tzinfo is None:
        dt = TZ_UTC.localize(dt)
    return dt.astimezone(TZ_US)


def to_tase_time(dt: datetime) -> datetime:
    """Convert any timezone-aware datetime to Israel time."""
    if dt.tzinfo is None:
        dt = TZ_UTC.localize(dt)
    return dt.astimezone(TZ_TASE)


def market_status() -> dict[str, bool | str]:
    """Return current open/closed status for both markets.

    Returns:
        Dict with keys: nyse_open, tase_open, tase_preopen, tase_friday,
        us_time, tase_time.
    """
    return {
        "nyse_open": is_nyse_open(),
        "tase_open": is_tase_open(),
        "tase_preopen": is_tase_preopen(),
        "tase_friday": is_friday_session(),
        "us_time": now_us().strftime("%Y-%m-%d %H:%M:%S %Z"),
        "tase_time": now_tase().strftime("%Y-%m-%d %H:%M:%S %Z"),
    }
