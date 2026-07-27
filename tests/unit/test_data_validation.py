"""Unit tests for the Stooq provider + cross-provider data validation."""

from datetime import timedelta

import pandas as pd
import pytest
from src.data.provider import NotSupportedError
from src.data.providers.stooq_provider import StooqProvider
from src.data.validation import (
    DataQualityVerdict,
    check_staleness,
    consensus_price,
    validate_ohlcv,
)
from src.utils.timezone_utils import now_utc

_STOOQ_CSV = (
    "Date,Open,High,Low,Close,Volume\n"
    "2026-07-01,100.0,101.0,99.0,100.5,1000000\n"
    "2026-07-02,100.5,102.0,100.0,101.5,1200000\n"
)


# ── Stooq provider ───────────────────────────────────────────────────────────


def test_stooq_symbol_mapping():
    assert StooqProvider._to_stooq_symbol("AAPL") == "aapl.us"


def test_stooq_rejects_dotted_symbol():
    with pytest.raises(NotSupportedError):
        StooqProvider._to_stooq_symbol("TEVA.TA")


@pytest.mark.asyncio
async def test_stooq_rejects_intraday_interval():
    with pytest.raises(NotSupportedError):
        await StooqProvider().fetch_ohlcv("AAPL", interval="1m")


def test_stooq_parse_csv_shape():
    df = StooqProvider._parse_csv(_STOOQ_CSV, "AAPL")
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert df.index.name == "Date"
    assert float(df["Close"].iloc[-1]) == 101.5


def test_stooq_parse_no_data_returns_empty():
    assert StooqProvider._parse_csv("No data\n", "AAPL").empty
    assert StooqProvider._parse_csv("", "AAPL").empty


# ── Consensus ────────────────────────────────────────────────────────────────


def test_consensus_single_source_is_failopen():
    v = consensus_price({"yfinance": 100.0})
    assert v.ok
    assert v.chosen_value == 100.0


def test_consensus_agreement_within_tolerance():
    v = consensus_price({"yfinance": 100.0, "stooq": 100.5}, tolerance_pct=0.02)
    assert v.ok
    assert v.chosen_value == pytest.approx(100.25)
    assert v.flags == []


def test_consensus_flags_disagreement():
    v = consensus_price(
        {"yfinance": 100.0, "stooq": 130.0, "finnhub": 101.0}, tolerance_pct=0.02
    )
    assert not v.ok
    assert "disagree:stooq" in v.flags
    # Median (101.0) is chosen — robust to the outlier.
    assert v.chosen_value == 101.0


def test_consensus_no_sources():
    v = consensus_price({})
    assert v.ok  # fail-open
    assert v.chosen_value is None
    assert "no_sources" in v.flags


def test_consensus_ignores_none_values():
    v = consensus_price({"yfinance": 100.0, "stooq": None})
    assert v.ok
    assert v.chosen_value == 100.0


# ── Staleness ────────────────────────────────────────────────────────────────


def test_staleness_fresh_ok():
    assert not check_staleness(now_utc(), max_age_seconds=3600)


def test_staleness_old_flagged():
    old = now_utc() - timedelta(hours=2)
    assert check_staleness(old, max_age_seconds=3600)


# ── Structural reuse ─────────────────────────────────────────────────────────


def test_validate_ohlcv_passthrough_clean():
    df = pd.DataFrame(
        {"Close": [10.0, 10.1, 10.2, 10.3]},
        index=pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-04"]),
    )
    clean, report = validate_ohlcv(df, "AAPL")
    assert len(clean) == len(df)
    assert report.trimmed_bars == 0


def test_verdict_dataclass_defaults():
    v = DataQualityVerdict(ok=True)
    assert v.flags == []
    assert v.sources == {}
