"""Unit tests for fundamentals module — format helpers and CompanyProfile fields."""

import pytest
from src.quant.fundamentals import (
    CompanyProfile,
    _fmt_cap,
    _fmt_float,
    _fmt_pct,
    format_profile_english,
)

# ── Helpers ────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_fmt_cap_billions():
    assert _fmt_cap(1_500_000_000) == "$1.5B"


@pytest.mark.unit
def test_fmt_cap_millions():
    assert _fmt_cap(500_000_000) == "$500M"


@pytest.mark.unit
def test_fmt_cap_none():
    assert _fmt_cap(None) == "N/A"


@pytest.mark.unit
def test_fmt_float_basic():
    assert _fmt_float(3.14159, precision=2) == "3.14"


@pytest.mark.unit
def test_fmt_float_none():
    assert _fmt_float(None) == "N/A"


@pytest.mark.unit
def test_fmt_pct_decimal():
    # 0.023 → "2.30%"
    assert _fmt_pct(0.023) == "2.30%"


@pytest.mark.unit
def test_fmt_pct_none():
    assert _fmt_pct(None) == "N/A"


# ── CompanyProfile short interest fields ──────────────────────────────────────


def _make_profile(**kwargs) -> CompanyProfile:
    defaults = {
        "ticker": "AAPL",
        "name": "Apple Inc.",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "summary": "Apple makes iPhones.",
        "market_cap": 3_000_000_000_000,
        "pe_trailing": 29.5,
        "pe_forward": 27.0,
        "eps_trailing": 6.43,
        "eps_forward": 7.0,
        "dividend_yield": 0.005,
        "target_price_mean": 220.0,
        "week_52_high": 250.0,
        "week_52_low": 160.0,
        "currency": "USD",
        "employees": 150000,
        "exchange": "NASDAQ",
    }
    defaults.update(kwargs)
    return CompanyProfile(**defaults)


@pytest.mark.unit
def test_company_profile_short_pct_default_none():
    profile = _make_profile()
    assert profile.short_pct is None


@pytest.mark.unit
def test_company_profile_short_ratio_default_none():
    profile = _make_profile()
    assert profile.short_ratio is None


@pytest.mark.unit
def test_company_profile_short_fields_set():
    profile = _make_profile(short_pct=0.023, short_ratio=1.8)
    assert profile.short_pct == pytest.approx(0.023)
    assert profile.short_ratio == pytest.approx(1.8)


# ── format_profile_english short interest display ─────────────────────────────


@pytest.mark.unit
def test_format_profile_english_short_interest_shown():
    profile = _make_profile(short_pct=0.035, short_ratio=2.1)
    result = format_profile_english(profile)
    assert "Short Interest" in result
    assert "3.50%" in result
    assert "Days to Cover" in result
    assert "2.1" in result


@pytest.mark.unit
def test_format_profile_english_no_short_interest_when_none():
    profile = _make_profile(short_pct=None, short_ratio=None)
    result = format_profile_english(profile)
    assert "Short Interest" not in result


@pytest.mark.unit
def test_format_profile_english_short_pct_only_no_ratio():
    profile = _make_profile(short_pct=0.01, short_ratio=None)
    result = format_profile_english(profile)
    assert "Short Interest" in result
    assert "Days to Cover" not in result


@pytest.mark.unit
def test_format_profile_english_contains_basics():
    profile = _make_profile()
    result = format_profile_english(profile)
    assert "Apple Inc." in result
    assert "AAPL" in result
    assert "P/E" in result
    assert "52-Week" in result
