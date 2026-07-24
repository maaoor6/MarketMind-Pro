"""Unit tests for the free Treasury par-yield-curve fetcher (no network)."""

from src.trading.treasury import parse_yield_curve_csv

_CSV = (
    'Date,"1 Mo","2 Mo","3 Mo","6 Mo","1 Yr","2 Yr","3 Yr","5 Yr","7 Yr","10 Yr","20 Yr","30 Yr"\n'
    "07/23/2026,5.40,5.38,5.35,5.20,4.90,4.50,4.40,4.30,4.35,4.45,4.80,4.95\n"
    "07/22/2026,5.41,5.39,5.36,5.21,4.91,4.51,4.41,4.31,4.36,4.46,4.81,4.96\n"
)


def test_parse_yield_curve_latest_row():
    # 10Y (4.45) − 2Y (4.50) from the newest (first) row.
    spread = parse_yield_curve_csv(_CSV)
    assert spread is not None
    assert round(spread, 2) == -0.05


def test_parse_yield_curve_empty():
    assert parse_yield_curve_csv("") is None
    assert parse_yield_curve_csv("Date,junk\n") is None


def test_parse_yield_curve_missing_columns():
    csv_no_cols = "Date,foo,bar\n07/23/2026,1,2\n"
    assert parse_yield_curve_csv(csv_no_cols) is None
