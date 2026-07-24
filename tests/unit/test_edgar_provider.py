"""Unit tests for the SEC EDGAR provider + fundamentals cross-validation.

All tests use recorded payloads — no network access.
"""

import pytest
from src.data.provider import CAP_FUNDAMENTALS, NotSupportedError
from src.data.providers.edgar_provider import EdgarProvider
from src.data.validation import crosscheck_fundamentals

# Minimal companyfacts payload shaped like data.sec.gov's response.
_FACTS_PAYLOAD = {
    "cik": 320193,
    "entityName": "Apple Inc.",
    "facts": {
        "us-gaap": {
            "Revenues": {
                "units": {
                    "USD": [
                        {"end": "2024-09-30", "val": 391035000000, "form": "10-K"},
                        {"end": "2023-09-30", "val": 383285000000, "form": "10-K"},
                    ]
                }
            },
            "NetIncomeLoss": {
                "units": {"USD": [{"end": "2024-09-30", "val": 93736000000}]}
            },
            "EarningsPerShareDiluted": {
                "units": {"USD/shares": [{"end": "2024-09-30", "val": 6.08}]}
            },
        },
        "dei": {
            "EntityCommonStockSharesOutstanding": {
                "units": {"shares": [{"end": "2024-10-18", "val": 15000000000}]}
            }
        },
    },
}


def test_capabilities():
    assert EdgarProvider().supports(CAP_FUNDAMENTALS)


def test_parse_facts_extracts_latest():
    p = EdgarProvider()
    out = p._parse_facts(_FACTS_PAYLOAD, "AAPL")
    assert out["entity_name"] == "Apple Inc."
    assert out["source"] == "edgar"
    # Latest revenue (2024 end date) wins over the earlier one.
    assert out["revenue"] == 391035000000
    assert out["net_income"] == 93736000000
    assert out["eps_diluted"] == 6.08
    assert out["shares_outstanding"] == 15000000000


def test_latest_fact_picks_most_recent_end():
    val = EdgarProvider._latest_fact(_FACTS_PAYLOAD["facts"], ("Revenues",))
    assert val == 391035000000


def test_latest_fact_missing_concept_returns_none():
    assert EdgarProvider._latest_fact(_FACTS_PAYLOAD["facts"], ("Nonexistent",)) is None


@pytest.mark.asyncio
async def test_cik_lookup_missing_raises_not_supported(monkeypatch):
    p = EdgarProvider()

    async def _fake_map():
        return {"AAPL": 320193}

    monkeypatch.setattr(p, "_load_cik_map", _fake_map)
    with pytest.raises(NotSupportedError):
        await p._cik_for("ZZZZ")


# ── Cross-validation ────────────────────────────────────────────────────────


def test_crosscheck_fundamentals_agree():
    yf_info = {"sharesOutstanding": 15_100_000_000}
    edgar = {"shares_outstanding": 15_000_000_000}
    v = crosscheck_fundamentals(yf_info, edgar, tolerance_pct=0.05)
    assert v.ok


def test_crosscheck_fundamentals_disagree_flagged():
    yf_info = {"sharesOutstanding": 15_000_000_000}
    edgar = {"shares_outstanding": 30_000_000_000}  # 2x off
    v = crosscheck_fundamentals(yf_info, edgar, tolerance_pct=0.05)
    assert not v.ok
    assert any(f.startswith("fundamentals_disagree") for f in v.flags)


def test_crosscheck_fundamentals_failopen_missing_side():
    v = crosscheck_fundamentals({"sharesOutstanding": 15_000_000_000}, {})
    assert v.ok  # only one source → fail-open
