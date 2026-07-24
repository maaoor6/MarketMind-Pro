"""U.S. Treasury par-yield-curve fetcher — free, official, no API key.

The Treasury publishes the Daily Treasury Par Yield Curve Rates as public CSV
at ``home.treasury.gov``. This gives the 10Y−2Y spread — a high-weight macro
input — without a FRED key, so the macro gate's yield-curve signal no longer
goes neutral when ``FRED_API_KEY`` is empty. Fail-open: any error → None.
"""

from __future__ import annotations

import csv
import io

import httpx

from src.utils.logger import get_logger
from src.utils.timezone_utils import now_us

logger = get_logger(__name__)

_CSV_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/"
    "interest-rates/daily-treasury-rates.csv/{year}/all"
)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,*/*",
}


def parse_yield_curve_csv(text: str) -> float | None:
    """Return the latest 10Y−2Y par-yield spread from a Treasury CSV, or None."""
    try:
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
    except Exception as exc:  # noqa: BLE001
        logger.debug("treasury_parse_failed", error=type(exc).__name__)
        return None
    if not rows:
        return None
    # Rows are newest-first; the first row is the latest business day.
    latest = rows[0]
    try:
        y10 = float(latest["10 Yr"])
        y2 = float(latest["2 Yr"])
    except (KeyError, TypeError, ValueError):
        return None
    return y10 - y2


async def treasury_yield_curve(timeout: float = 8.0) -> float | None:
    """Fetch the latest 10Y−2Y Treasury par-yield spread (free, no key)."""
    year = now_us().year
    params = {
        "type": "daily_treasury_yield_curve",
        "field_tdr_date_value": str(year),
        "_format": "csv",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=_HEADERS) as client:
            resp = await client.get(_CSV_URL.format(year=year), params=params)
            resp.raise_for_status()
            text = resp.text
    except Exception as exc:  # noqa: BLE001 — macro input must fail open to neutral
        logger.debug("treasury_fetch_failed", error=type(exc).__name__)
        return None
    return parse_yield_curve_csv(text)
