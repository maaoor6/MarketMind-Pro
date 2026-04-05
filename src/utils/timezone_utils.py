"""Timezone-aware utilities for TASE and US market hours."""

from datetime import datetime, time

import pytz

from src.utils.config import settings

TZ_US = pytz.timezone(settings.timezone_us)
TZ_TASE = pytz.timezone(settings.timezone_tase)
TZ_UTC = pytz.UTC

# TASE trading hours (Israel time): Sun–Thu 09:59–17:25
TASE_OPEN = time(9, 59)
TASE_CLOSE = time(17, 25)
TASE_TRADING_DAYS = {0, 1, 2, 3, 4}  # Mon=0...Sun=6 in Python, but TASE: Sun=6, Mon=0

# NYSE/NASDAQ trading hours (ET): Mon–Fri 09:30–16:00
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
    """Return True if NYSE is currently in regular trading hours."""
    now = now_us()
    if now.weekday() not in NYSE_TRADING_DAYS:
        return False
    current_time = now.time()
    return NYSE_OPEN <= current_time <= NYSE_CLOSE


def is_tase_open() -> bool:
    """Return True if TASE is currently in regular trading hours.

    TASE trades Sun–Thu in Israel local time.
    In Python weekday(): Sunday=6, Mon=0, Tue=1, Wed=2, Thu=3.
    """
    now = now_tase()
    weekday = now.weekday()
    # TASE: Mon=0, Tue=1, Wed=2, Thu=3, Sun=6
    if weekday not in {0, 1, 2, 3, 6}:
        return False
    current_time = now.time()
    return TASE_OPEN <= current_time <= TASE_CLOSE


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


def market_status() -> dict[str, bool]:
    """Return current open/closed status for both markets."""
    return {
        "nyse_open": is_nyse_open(),
        "tase_open": is_tase_open(),
        "us_time": now_us().strftime("%Y-%m-%d %H:%M:%S %Z"),
        "tase_time": now_tase().strftime("%Y-%m-%d %H:%M:%S %Z"),
    }
