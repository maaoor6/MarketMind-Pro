"""Unit tests for the RiskOverseer agent + pure factor math (Phase 2A.5)."""

import pytest
from src.trading.infra_agents import CycleContext, RiskOverseerAgent
from src.trading.macro_gate import MacroState
from src.trading.risk_factors import (
    beta,
    correlation,
    daily_returns,
    max_correlation_to_held,
    portfolio_beta,
)


class _Pos:
    def __init__(self, value: float) -> None:
        self.market_value = value
        self.quantity = 1
        self.current_price = value
        self.avg_price = value


class _Portfolio:
    def __init__(self, positions: dict, total_value: float) -> None:
        self.positions = positions
        self.total_value = total_value


def _cycle(contexts, portfolio):
    state = MacroState(
        regime="BULL", decision="ON", size_mult=1.0, confidence_factor=1.0
    )
    return CycleContext(
        contexts={t: object() for t in contexts},
        portfolio=portfolio,
        macro=state,
        regime="BULL",
    )


# ── Pure math ────────────────────────────────────────────────────────────────


def test_daily_returns():
    assert daily_returns([100, 110, 99]) == pytest.approx([0.1, -0.1], rel=1e-6)


def test_correlation_perfect_positive():
    a = [0.01, 0.02, -0.01, 0.03]
    assert correlation(a, a) == pytest.approx(1.0, rel=1e-6)


def test_correlation_negative():
    a = [0.01, 0.02, -0.01, 0.03]
    b = [-0.01, -0.02, 0.01, -0.03]
    assert correlation(a, b) == pytest.approx(-1.0, rel=1e-6)


def test_beta_of_market_is_one():
    m = [0.01, -0.02, 0.03, -0.01]
    assert beta(m, m) == pytest.approx(1.0, rel=1e-6)


def test_portfolio_beta_weighted():
    weights = {"A": 100.0, "B": 100.0}
    betas = {"A": 0.8, "B": 1.6}
    assert portfolio_beta(weights, betas) == pytest.approx(1.2)


def test_max_correlation_to_held():
    cand = [0.01, 0.02, -0.01, 0.03]
    held = {"X": [-0.01, -0.02, 0.01, -0.03], "Y": [0.01, 0.02, -0.01, 0.03]}
    assert max_correlation_to_held(cand, held) == pytest.approx(1.0, rel=1e-6)


# ── Agent ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_overseer_blocks_correlated_candidate():
    # Candidate NVDA is perfectly correlated with the held AAPL.
    series = {
        "SPY": [0.01, -0.01, 0.02, -0.02, 0.01, 0.0],
        "AAPL": [0.02, -0.02, 0.03, -0.01, 0.02, 0.01],
        "NVDA": [0.02, -0.02, 0.03, -0.01, 0.02, 0.01],
    }

    async def fetch(t):
        return series.get(t)

    agent = RiskOverseerAgent(fetch_returns=fetch)
    portfolio = _Portfolio({"AAPL": _Pos(1000)}, 2000)
    report = await agent.observe(_cycle(["AAPL", "NVDA"], portfolio))
    assert "NVDA" in report.block_buys
    assert not report.ok


@pytest.mark.asyncio
async def test_overseer_high_beta_shrinks_budget():
    # Holding swings 2x the market → portfolio beta ~2 > 1.20 cap.
    series = {
        "SPY": [0.01, -0.01, 0.02, -0.02, 0.03],
        "LEV": [0.02, -0.02, 0.04, -0.04, 0.06],
    }

    async def fetch(t):
        return series.get(t)

    agent = RiskOverseerAgent(fetch_returns=fetch)
    portfolio = _Portfolio({"LEV": _Pos(1000)}, 1000)
    report = await agent.observe(_cycle(["LEV"], portfolio))
    assert report.size_mult < 1.0
    assert report.detail.get("portfolio_beta", 0) > 1.2


@pytest.mark.asyncio
async def test_overseer_sector_cluster_blocks():
    async def sector_of(t):
        return "Technology"  # everything tech → cluster fills

    agent = RiskOverseerAgent(sector_of=sector_of)
    portfolio = _Portfolio({"AAPL": _Pos(400)}, 1000)  # 40% tech >= 0.30 cap
    report = await agent.observe(_cycle(["AAPL", "MSFT"], portfolio))
    assert "MSFT" in report.block_buys


@pytest.mark.asyncio
async def test_overseer_failopen_no_portfolio():
    agent = RiskOverseerAgent(fetch_returns=None, sector_of=None)
    report = await agent.observe(_cycle(["AAPL"], None))
    assert report.ok
    assert report.block_buys == set()
