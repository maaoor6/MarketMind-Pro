"""Unit tests for fundamentals module — format helpers and CompanyProfile fields."""

import pytest
from src.quant.fundamentals import (
    CompanyProfile,
    EarningsReport,
    _fmt_cap,
    _fmt_float,
    _fmt_pct,
    _fmt_revenue,
    format_earnings_english,
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


# ── _fmt_revenue ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_fmt_revenue_billions():
    assert _fmt_revenue(12_500_000_000) == "$12.5B"


@pytest.mark.unit
def test_fmt_revenue_millions():
    assert _fmt_revenue(450_000_000) == "$450M"


@pytest.mark.unit
def test_fmt_revenue_small():
    assert _fmt_revenue(500_000) == "$500,000"


@pytest.mark.unit
def test_fmt_revenue_none():
    assert _fmt_revenue(None) == "N/A"


# ── EarningsReport helpers ────────────────────────────────────────────────────


def _make_report(**kwargs) -> EarningsReport:
    defaults = {
        "ticker": "AAPL",
        "quarter": "Q1 2026",
        "report_date": "Jan 30, 2026",
        "eps_actual": 2.40,
        "eps_estimate": 2.35,
        "eps_surprise_pct": 2.13,
        "revenue_actual": 124_300_000_000,
        "revenue_estimate": 123_000_000_000,
        "revenue_surprise_pct": 1.06,
        "revenue_growth_yoy": 0.04,
        "gross_margin": 0.46,
        "beat_eps": True,
        "beat_revenue": True,
    }
    defaults.update(kwargs)
    return EarningsReport(**defaults)


# ── format_earnings_english ───────────────────────────────────────────────────


@pytest.mark.unit
def test_format_earnings_header():
    report = _make_report()
    result = format_earnings_english(report)
    assert "Q1 2026 Earnings" in result
    assert "Jan 30, 2026" in result


@pytest.mark.unit
def test_format_earnings_eps_beat():
    report = _make_report(beat_eps=True, eps_surprise_pct=2.13)
    result = format_earnings_english(report)
    assert "✅ Beat" in result
    assert "$2.40" in result
    assert "$2.35" in result
    assert "+2.1%" in result


@pytest.mark.unit
def test_format_earnings_eps_miss():
    report = _make_report(
        beat_eps=False, eps_surprise_pct=-5.0, eps_actual=1.80, eps_estimate=1.90
    )
    result = format_earnings_english(report)
    assert "❌ Miss" in result
    assert "-5.0%" in result


@pytest.mark.unit
def test_format_earnings_revenue_shown():
    report = _make_report()
    result = format_earnings_english(report)
    assert "$124.3B" in result
    assert "$123.0B" in result


@pytest.mark.unit
def test_format_earnings_yoy_growth():
    report = _make_report(revenue_growth_yoy=0.051, gross_margin=0.463)
    result = format_earnings_english(report)
    assert "+5.1%" in result
    assert "46.3%" in result


@pytest.mark.unit
def test_format_earnings_negative_yoy():
    report = _make_report(revenue_growth_yoy=-0.03)
    result = format_earnings_english(report)
    assert "-3.0%" in result


@pytest.mark.unit
def test_format_earnings_no_estimate():
    """When all estimates are None, no Beat/Miss shown anywhere."""
    report = _make_report(
        eps_estimate=None,
        eps_surprise_pct=None,
        beat_eps=None,
        revenue_estimate=None,
        revenue_surprise_pct=None,
        beat_revenue=None,
    )
    result = format_earnings_english(report)
    assert "✅" not in result
    assert "❌" not in result
    assert "$2.40" in result


@pytest.mark.unit
def test_format_earnings_no_eps():
    """When eps_actual is None, EPS line is omitted entirely."""
    report = _make_report(
        eps_actual=None, eps_estimate=None, beat_eps=None, eps_surprise_pct=None
    )
    result = format_earnings_english(report)
    assert "EPS" not in result


@pytest.mark.unit
def test_format_earnings_with_headlines():
    report = _make_report()
    headlines = [
        {"title": "Apple beats Q1 earnings expectations", "source": "Reuters"},
        {"title": "AAPL revenue tops forecast", "source": "Bloomberg"},
    ]
    result = format_earnings_english(report, headlines)
    assert "Apple beats Q1 earnings" in result
    assert "Reuters" in result
    assert "Bloomberg" in result


@pytest.mark.unit
def test_format_earnings_headlines_capped_at_5():
    report = _make_report()
    headlines = [{"title": f"Headline {i}", "source": "src"} for i in range(10)]
    result = format_earnings_english(report, headlines)
    assert result.count("Headline") == 5


@pytest.mark.unit
def test_format_earnings_html_escape():
    """Quarter/date with special chars must be HTML-escaped."""
    report = _make_report(quarter="Q1 <2026>", report_date="Jan & Feb")
    result = format_earnings_english(report)
    assert "<2026>" not in result
    assert "&amp;" in result or "Jan" in result
