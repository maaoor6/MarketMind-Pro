"""High-impact macro-event calendar — blackout windows around FOMC/CPI/NFP.

Zero paid-API cost: NFP (US jobs report) is the first Friday of each month and
is computed deterministically; FOMC decisions and CPI releases follow published
schedules and are seeded here (refresh annually — the module fails open beyond
the seeded horizon, i.e. no blackout, so a stale table never *blocks* trading).

The gate reduces or halts new entries inside a window; it never forces exits.
All event times are US/Eastern (announcement time), tz-aware.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from src.utils.timezone_utils import TZ_US, now_us

# FOMC rate decisions — 2:00 PM ET on the second (final) day of each meeting.
# Source: federalreserve.gov meeting calendar. Refresh annually.
_FOMC_DATES: tuple[date, ...] = (
    date(2025, 1, 29),
    date(2025, 3, 19),
    date(2025, 5, 7),
    date(2025, 6, 18),
    date(2025, 7, 30),
    date(2025, 9, 17),
    date(2025, 10, 29),
    date(2025, 12, 10),
    date(2026, 1, 28),
    date(2026, 3, 18),
    date(2026, 4, 29),
    date(2026, 6, 17),
    date(2026, 7, 29),
    date(2026, 9, 16),
    date(2026, 10, 28),
    date(2026, 12, 9),
)

# CPI releases — 8:30 AM ET, per the BLS schedule. Refresh annually.
_CPI_DATES: tuple[date, ...] = (
    date(2025, 1, 15),
    date(2025, 2, 12),
    date(2025, 3, 12),
    date(2025, 4, 10),
    date(2025, 5, 13),
    date(2025, 6, 11),
    date(2025, 7, 15),
    date(2025, 8, 12),
    date(2025, 9, 11),
    date(2025, 10, 15),
    date(2025, 11, 13),
    date(2025, 12, 10),
    date(2026, 1, 14),
    date(2026, 2, 11),
    date(2026, 3, 11),
    date(2026, 4, 10),
    date(2026, 5, 12),
    date(2026, 6, 10),
    date(2026, 7, 14),
    date(2026, 8, 12),
    date(2026, 9, 11),
    date(2026, 10, 14),
    date(2026, 11, 12),
    date(2026, 12, 10),
)

_FOMC_TIME = time(14, 0)  # 2:00 PM ET
_RELEASE_TIME = time(8, 30)  # CPI / NFP 8:30 AM ET


@dataclass(frozen=True)
class MacroEvent:
    kind: str  # "FOMC" | "CPI" | "NFP"
    when: datetime  # tz-aware US/Eastern announcement time


def _first_friday(year: int, month: int) -> date:
    """First Friday of the month (US non-farm-payrolls release day)."""
    d = date(year, month, 1)
    # weekday(): Mon=0 … Fri=4.
    return d + timedelta(days=(4 - d.weekday()) % 7)


def _nfp_events(around: date) -> list[MacroEvent]:
    """NFP events for the month of ``around`` and its neighbors."""
    events: list[MacroEvent] = []
    for delta in (-1, 0, 1):
        month = around.month + delta
        year = around.year
        if month < 1:
            month, year = 12, year - 1
        elif month > 12:
            month, year = 1, year + 1
        friday = _first_friday(year, month)
        events.append(
            MacroEvent(
                "NFP",
                TZ_US.localize(datetime.combine(friday, _RELEASE_TIME)),
            )
        )
    return events


def _seeded_events() -> list[MacroEvent]:
    events = [
        MacroEvent("FOMC", TZ_US.localize(datetime.combine(d, _FOMC_TIME)))
        for d in _FOMC_DATES
    ]
    events += [
        MacroEvent("CPI", TZ_US.localize(datetime.combine(d, _RELEASE_TIME)))
        for d in _CPI_DATES
    ]
    return events


def upcoming_events(reference: datetime | None = None) -> list[MacroEvent]:
    """All known events near ``reference`` (default now), sorted by time."""
    ref = reference or now_us()
    events = _seeded_events() + _nfp_events(ref.date())
    return sorted(events, key=lambda e: e.when)


def active_blackout(
    window_hours: float,
    reference: datetime | None = None,
) -> MacroEvent | None:
    """The high-impact event whose ±``window_hours`` window contains now, if any.

    Fail-open: returns None when no seeded/derived event is within range.
    """
    ref = reference or now_us()
    if ref.tzinfo is None:
        ref = TZ_US.localize(ref)
    window = timedelta(hours=window_hours)
    for event in upcoming_events(ref):
        if abs((event.when - ref).total_seconds()) <= window.total_seconds():
            return event
    return None
