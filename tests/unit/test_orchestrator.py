"""Unit tests for the Orchestrator decision helpers (offline, no StockArena)."""

from unittest.mock import AsyncMock

import pytest
from src.trading.macro_gate import MacroState
from src.trading.orchestrator import Orchestrator
from src.trading.stockarena_client import Position
from src.trading.strategies import Action, Strategy, StrategyContext, StrategySignal


class _FakeStrategy(Strategy):
    def __init__(self, name: str, action: Action, confidence: float) -> None:
        self.name = name
        self._action = action
        self._confidence = confidence

    def evaluate(self, ctx: StrategyContext) -> StrategySignal:
        if self._action == Action.HOLD:
            return self._hold(ctx, "hold")
        return self._signal(ctx, self._action, self._confidence, "fake")


def _ctx(ticker="AAPL", price=100.0, position=None, ret_3m=None) -> StrategyContext:
    return StrategyContext(
        ticker=ticker,
        signals={"price": price, "ret_3m": ret_3m, "moving_averages": {}},
        fibonacci=None,
        weekly={},
        monthly={},
        momentum=None,
        position=position,
    )


@pytest.fixture
def orch():
    o = Orchestrator(quant=None)
    o._allocator.record_signal = AsyncMock()
    o._risk.in_min_holding = AsyncMock(return_value=False)
    return o


_STATE = MacroState(regime="BULL", decision="ON", size_mult=1.0, confidence_factor=1.0)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_decide_buys_from_active_agent(orch):
    orch._strategies = [_FakeStrategy("ts_momentum", Action.BUY, 0.8)]
    decision = await orch._decide_orchestrated(
        _ctx(), {"ts_momentum": 1.0}, _STATE, {"ts_momentum"}
    )
    assert decision is not None
    action, sig, _ = decision
    assert action == "buy"
    assert sig.strategy == "ts_momentum"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_decide_skips_buy_from_inactive_agent(orch):
    orch._strategies = [_FakeStrategy("ts_momentum", Action.BUY, 0.8)]
    # ts_momentum not in the active set (e.g. Core inactive in BEAR).
    decision = await orch._decide_orchestrated(
        _ctx(), {"ts_momentum": 1.0}, _STATE, active_names=set()
    )
    assert decision is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_decide_sell_allowed_from_any_owned_strategy(orch):
    held = Position(ticker="AAPL", quantity=10, avg_price=90.0, current_price=100.0)
    orch._strategies = [_FakeStrategy("dip_buyer", Action.SELL, 0.9)]
    # dip_buyer not in active set, but a SELL on a held position still fires.
    decision = await orch._decide_orchestrated(
        _ctx(position=held), {"dip_buyer": 1.0}, _STATE, active_names=set()
    )
    assert decision is not None
    assert decision[0] == "sell"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_decide_disabled_strategy_filtered(orch):
    orch._strategies = [_FakeStrategy("ts_momentum", Action.BUY, 0.8)]
    # weight 0 → disabled by the learning gate.
    decision = await orch._decide_orchestrated(
        _ctx(), {"ts_momentum": 0.0}, _STATE, {"ts_momentum"}
    )
    assert decision is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_decide_applies_confidence_factor(orch):
    orch._strategies = [_FakeStrategy("ts_momentum", Action.BUY, 0.8)]
    state = MacroState(
        regime="BEAR", decision="SCALED", size_mult=0.5, confidence_factor=0.5
    )
    _, sig, _ = await orch._decide_orchestrated(
        _ctx(), {"ts_momentum": 1.0}, state, {"ts_momentum"}
    )
    assert sig.confidence == pytest.approx(0.4)  # 0.8 × 0.5


@pytest.mark.unit
def test_inject_sector_ranks(orch):
    contexts = {
        "XLK": _ctx("XLK", ret_3m=0.10),
        "XLF": _ctx("XLF", ret_3m=0.02),
        "XLE": _ctx("XLE", ret_3m=-0.05),
    }
    orch._inject_sector_ranks(contexts)
    ranks = contexts["XLK"].cross_section["sector_rank"]
    assert ranks["XLK"] == pytest.approx(1.0)  # strongest
    assert ranks["XLE"] < ranks["XLF"] < ranks["XLK"]
