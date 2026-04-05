"""Unit tests for timezone-aware market hours utilities."""

from datetime import datetime

import pytest
import pytz
from freezegun import freeze_time

from src.utils.timezone_utils import (
    is_nyse_open,
    is_tase_open,
    market_status,
    to_tase_time,
    to_us_time,
    TZ_US,
    TZ_TASE,
)


@pytest.mark.unit
@freeze_time("2024-01-15 14:30:00", tz_offset=0)  # Mon 14:30 UTC → 09:30 ET
def test_nyse_open_at_930_et():
    assert is_nyse_open() is True


@pytest.mark.unit
@freeze_time("2024-01-15 21:30:00", tz_offset=0)  # Mon 21:30 UTC → 16:30 ET
def test_nyse_closed_after_16():
    assert is_nyse_open() is False


@pytest.mark.unit
@freeze_time("2024-01-13 14:30:00", tz_offset=0)  # Saturday
def test_nyse_closed_weekend():
    assert is_nyse_open() is False


@pytest.mark.unit
@freeze_time("2024-01-14 10:00:00", tz_offset=0)  # Sunday 10:00 UTC → 12:00 IST
def test_tase_open_on_sunday():
    # Sunday in Israel is a trading day; 12:00 IST is within 09:59–17:25
    assert is_tase_open() is True


@pytest.mark.unit
@freeze_time("2024-01-13 10:00:00", tz_offset=0)  # Saturday
def test_tase_closed_on_saturday():
    # Saturday is the Jewish Sabbath — TASE is closed
    assert is_tase_open() is False


@pytest.mark.unit
def test_market_status_returns_all_keys():
    status = market_status()
    assert "nyse_open" in status
    assert "tase_open" in status
    assert "us_time" in status
    assert "tase_time" in status


@pytest.mark.unit
def test_to_us_time_conversion():
    utc_dt = datetime(2024, 1, 15, 14, 30, tzinfo=pytz.UTC)
    us_dt = to_us_time(utc_dt)
    assert us_dt.tzinfo is not None
    assert us_dt.hour == 9  # 14:30 UTC = 09:30 ET (EST, UTC-5)
    assert us_dt.minute == 30


@pytest.mark.unit
def test_to_tase_time_conversion():
    utc_dt = datetime(2024, 1, 15, 8, 0, tzinfo=pytz.UTC)
    tase_dt = to_tase_time(utc_dt)
    assert tase_dt.tzinfo is not None
    assert tase_dt.hour == 10  # 08:00 UTC = 10:00 IST (UTC+2)
