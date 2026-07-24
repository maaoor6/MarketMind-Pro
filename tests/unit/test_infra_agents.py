"""Unit tests for the infra-agent seam (Phase 2A): DataValidation + Sentiment."""

from unittest.mock import AsyncMock

import pytest
from src.trading.infra_agents import (
    AgentReport,
    CycleContext,
    DataValidationAgent,
    SentimentAgent,
    _extract_score,
    aggregate_reports,
    build_infra_agents,
)
from src.trading.macro_gate import MacroState


def _cycle(tickers):
    contexts = {t: object() for t in tickers}
    state = MacroState(
        regime="BULL", decision="ON", size_mult=1.0, confidence_factor=1.0
    )
    return CycleContext(contexts=contexts, portfolio=None, macro=state, regime="BULL")


# ── Aggregation ──────────────────────────────────────────────────────────────


def test_aggregate_union_blocks_and_min_scale():
    r1 = AgentReport(agent="a", block_buys={"AAPL"}, buy_scale={"MSFT": 0.5})
    r2 = AgentReport(
        agent="b", block_buys={"TSLA"}, buy_scale={"MSFT": 0.8}, size_mult=0.5
    )
    d = aggregate_reports([r1, r2])
    assert d.blocked == {"AAPL", "TSLA"}
    assert d.scale_for("MSFT") == 0.5  # min of 0.5 and 0.8
    assert d.size_mult == 0.5
    assert len(d.reports) == 2


def test_aggregate_clamps_size_mult():
    d = aggregate_reports([AgentReport(agent="a", size_mult=2.0)])
    assert d.size_mult == 1.0
    d2 = aggregate_reports([AgentReport(agent="a", size_mult=-1.0)])
    assert d2.size_mult == 0.0


def test_aggregate_empty_is_no_tightening():
    d = aggregate_reports([])
    assert d.blocked == set()
    assert d.size_mult == 1.0
    assert d.scale_for("ANY") == 1.0


# ── DataValidationAgent ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_data_validation_blocks_flagged(monkeypatch):
    agent = DataValidationAgent()

    async def _fake_get(key):
        if key == "data:quality:AAPL":
            return {"ok": False, "flags": ["disagree:stooq"]}
        return {"ok": True}

    monkeypatch.setattr("src.trading.infra_agents.cache.get", _fake_get)
    report = await agent.observe(_cycle(["AAPL", "MSFT"]))
    assert "AAPL" in report.block_buys
    assert "MSFT" not in report.block_buys
    assert not report.ok


@pytest.mark.asyncio
async def test_data_validation_failopen_missing(monkeypatch):
    agent = DataValidationAgent()
    monkeypatch.setattr(
        "src.trading.infra_agents.cache.get", AsyncMock(return_value=None)
    )
    report = await agent.observe(_cycle(["AAPL"]))
    assert report.block_buys == set()
    assert report.ok


# ── SentimentAgent ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sentiment_vetoes_very_negative(monkeypatch):
    agent = SentimentAgent()

    async def _fake_get(key):
        return (
            {"AAPL": {"score": -0.8}, "MSFT": {"score": -0.3}, "NVDA": {"score": 0.5}}[
                key.split(":")[-1]
            ]
            if key.split(":")[-1] in {"AAPL", "MSFT", "NVDA"}
            else None
        )

    monkeypatch.setattr("src.trading.infra_agents.cache.get", _fake_get)
    report = await agent.observe(_cycle(["AAPL", "MSFT", "NVDA"]))
    assert "AAPL" in report.block_buys  # -0.8 <= veto -0.5
    assert report.buy_scale.get("MSFT") == 0.5  # -0.3 in caution band
    assert "NVDA" not in report.block_buys and "NVDA" not in report.buy_scale


def test_extract_score_shapes():
    assert _extract_score({"score": -0.4}) == -0.4
    assert _extract_score(0.2) == 0.2
    assert _extract_score({"nope": 1}) is None
    assert _extract_score(None) is None


def test_build_infra_agents_default():
    agents = build_infra_agents()
    names = {a.name for a in agents}
    assert "data_validation" in names
    assert "sentiment" in names
