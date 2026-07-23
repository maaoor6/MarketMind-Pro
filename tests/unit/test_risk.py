"""Unit tests for the RiskManager — sizing, exits, circuit breaker."""

import pytest
from src.trading import risk as risk_module
from src.trading.risk import RiskManager
from src.trading.stockarena_client import Portfolio, Position
from src.trading.strategies import Action, StrategySignal
from src.utils.config import settings


class FakeCache:
    """In-memory stand-in for the Redis cache singleton."""

    def __init__(self) -> None:
        self.store: dict[str, object] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ttl=None):
        self.store[key] = value

    async def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture
def fake_cache(monkeypatch) -> FakeCache:
    fake = FakeCache()
    monkeypatch.setattr(risk_module, "cache", fake)
    return fake


def make_portfolio(
    cash: float = 10000.0,
    total_value: float = 10000.0,
    positions: dict[str, Position] | None = None,
) -> Portfolio:
    return Portfolio(
        cash=cash, total_value=total_value, return_pct=0.0, positions=positions or {}
    )


def buy_signal(price: float = 100.0, confidence: float = 0.8) -> StrategySignal:
    return StrategySignal(
        strategy="momentum_daily",
        ticker="AAPL",
        action=Action.BUY,
        confidence=confidence,
        reason="test",
        price=price,
    )


def sell_signal(ticker: str = "AAPL") -> StrategySignal:
    return StrategySignal(
        strategy="momentum_daily",
        ticker=ticker,
        action=Action.SELL,
        confidence=0.9,
        reason="test",
        price=100.0,
    )


# ── size_buy ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_size_buy_produces_positive_int_quantity():
    plan = RiskManager().size_buy(buy_signal(), weight=1.0, portfolio=make_portfolio())
    assert plan is not None
    assert isinstance(plan.quantity, int)
    assert plan.quantity >= 1
    assert plan.quantity * plan.est_price <= 10000


@pytest.mark.unit
def test_size_buy_respects_max_position_pct():
    plan = RiskManager().size_buy(
        buy_signal(confidence=1.0), weight=1.0, portfolio=make_portfolio()
    )
    assert plan is not None
    assert plan.quantity * plan.est_price <= settings.max_position_pct * 10000 + 1e-6


@pytest.mark.unit
def test_size_buy_rejects_when_below_min_notional():
    # Tiny cash → budget under min_trade_notional
    portfolio = make_portfolio(cash=150.0, total_value=150.0)
    plan = RiskManager().size_buy(buy_signal(), weight=1.0, portfolio=portfolio)
    assert plan is None


@pytest.mark.unit
def test_size_buy_leaves_cash_for_commission():
    plan = RiskManager().size_buy(
        buy_signal(confidence=1.0), weight=1.0, portfolio=make_portfolio()
    )
    assert plan is not None
    notional = plan.quantity * plan.est_price
    assert notional + settings.trade_commission <= 10000


@pytest.mark.unit
def test_size_buy_rejects_when_commission_ratio_too_high(monkeypatch):
    # $5 round trip on a small order breaches max_commission_pct (1%) —
    # force a tiny budget by shrinking the position cap.
    monkeypatch.setattr(settings, "min_trade_notional", 100.0)
    monkeypatch.setattr(settings, "max_position_pct", 0.03)  # cap ≈ $300
    plan = RiskManager().size_buy(
        buy_signal(price=100.0, confidence=1.0),
        weight=1.0,
        portfolio=make_portfolio(),
    )
    # $300 notional → $5/300 = 1.67% > 1% → rejected
    assert plan is None


@pytest.mark.unit
def test_size_buy_rejects_low_confidence():
    sig = buy_signal(confidence=settings.min_confidence - 0.01)
    assert RiskManager().size_buy(sig, weight=1.0, portfolio=make_portfolio()) is None


@pytest.mark.unit
def test_size_buy_rejects_new_ticker_at_max_positions():
    positions = {
        f"T{i}": Position(ticker=f"T{i}", quantity=1, avg_price=100.0)
        for i in range(settings.max_open_positions)
    }
    portfolio = make_portfolio(positions=positions)
    assert RiskManager().size_buy(buy_signal(), weight=1.0, portfolio=portfolio) is None


@pytest.mark.unit
def test_size_buy_halved_in_extended_hours():
    regular = RiskManager().size_buy(
        buy_signal(confidence=0.9), weight=1.0, portfolio=make_portfolio()
    )
    extended = RiskManager().size_buy(
        buy_signal(confidence=0.9),
        weight=1.0,
        portfolio=make_portfolio(),
        extended_hours=True,
    )
    assert regular is not None and extended is not None
    assert extended.quantity < regular.quantity


@pytest.mark.unit
def test_size_buy_extended_hours_requires_high_confidence():
    sig = buy_signal(confidence=settings.extended_hours_min_confidence - 0.05)
    plan = RiskManager().size_buy(
        sig, weight=1.0, portfolio=make_portfolio(), extended_hours=True
    )
    assert plan is None


# ── validate_sell ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_validate_sell_full_exit_only():
    portfolio = make_portfolio(
        positions={"AAPL": Position(ticker="AAPL", quantity=7, avg_price=90.0)}
    )
    plan = RiskManager().validate_sell(sell_signal(), portfolio)
    assert plan is not None
    assert plan.quantity == 7


@pytest.mark.unit
def test_validate_sell_never_shorts():
    plan = RiskManager().validate_sell(sell_signal("MSFT"), make_portfolio())
    assert plan is None


# ── check_exits ────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stop_loss_triggers_full_exit(fake_cache):
    pos = Position(ticker="AAPL", quantity=10, avg_price=100.0, current_price=94.0)
    portfolio = make_portfolio(positions={"AAPL": pos})
    plans = await RiskManager().check_exits(portfolio, contexts={})
    assert len(plans) == 1
    assert plans[0].action == "sell"
    assert plans[0].quantity == 10
    assert "stop-loss" in plans[0].reason


@pytest.mark.unit
@pytest.mark.asyncio
async def test_take_profit_cap_triggers_exit(fake_cache):
    pos = Position(ticker="AAPL", quantity=10, avg_price=100.0, current_price=111.0)
    portfolio = make_portfolio(positions={"AAPL": pos})
    plans = await RiskManager().check_exits(portfolio, contexts={})
    assert len(plans) == 1
    assert "take-profit" in plans[0].reason


@pytest.mark.unit
@pytest.mark.asyncio
async def test_take_profit_waits_for_commission_to_clear(fake_cache):
    # +10.2% gross on a $1000 position is below +10% + 0.5% fee drag → hold.
    pos = Position(ticker="AAPL", quantity=10, avg_price=100.0, current_price=110.2)
    portfolio = make_portfolio(positions={"AAPL": pos})
    plans = await RiskManager().check_exits(portfolio, contexts={})
    assert plans == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_exit_within_normal_range(fake_cache):
    pos = Position(ticker="AAPL", quantity=10, avg_price=100.0, current_price=102.0)
    portfolio = make_portfolio(positions={"AAPL": pos})
    plans = await RiskManager().check_exits(portfolio, contexts={})
    assert plans == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_trailing_stop_exits_after_pullback_from_high(fake_cache):
    # Position up 8%: high-water mark recorded at 108, then price falls to 103
    # (>4% below HWM) while still above stop-loss and below take-profit cap.
    fake_cache.store["trading:hwm:AAPL"] = 108.0
    pos = Position(ticker="AAPL", quantity=10, avg_price=96.0, current_price=103.0)
    portfolio = make_portfolio(positions={"AAPL": pos})
    plans = await RiskManager().check_exits(portfolio, contexts={})
    assert len(plans) == 1
    assert "trailing stop" in plans[0].reason


# ── circuit breaker ────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_circuit_breaker_arms_on_first_call_and_trips_on_drawdown(fake_cache):
    rm = RiskManager()
    assert await rm.circuit_breaker_tripped(make_portfolio(total_value=10000)) is False
    # Down 3.5% intraday → trips
    assert await rm.circuit_breaker_tripped(make_portfolio(total_value=9650)) is True
    # Stays halted for the day even if value recovers
    assert await rm.circuit_breaker_tripped(make_portfolio(total_value=10100)) is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_circuit_breaker_ignores_small_drawdown(fake_cache):
    rm = RiskManager()
    await rm.circuit_breaker_tripped(make_portfolio(total_value=10000))
    assert await rm.circuit_breaker_tripped(make_portfolio(total_value=9850)) is False


# ── minimum holding period (anti-churn) ────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_in_min_holding_true_right_after_entry(fake_cache):
    rm = RiskManager()
    await rm.set_entry_time("AAPL")
    assert await rm.in_min_holding("AAPL") is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_in_min_holding_false_without_entry_record(fake_cache):
    # Fail-open: unknown entry time must not block a sell.
    assert await RiskManager().in_min_holding("AAPL") is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_in_min_holding_expires_after_min_holding_hours(fake_cache):
    from datetime import timedelta

    from src.utils.timezone_utils import now_us

    old = now_us() - timedelta(hours=settings.min_holding_hours + 1)
    fake_cache.store["trading:entry_time:AAPL"] = old.isoformat()
    assert await RiskManager().in_min_holding("AAPL") is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_clear_position_state_removes_entry_time(fake_cache):
    rm = RiskManager()
    await rm.set_entry_time("AAPL")
    await rm.clear_position_state("AAPL")
    assert await rm.in_min_holding("AAPL") is False


# ── volatility-aware sizing ────────────────────────────────────────────


@pytest.mark.unit
def test_size_buy_shrinks_high_volatility_position():
    rm = RiskManager()
    calm = buy_signal()
    calm.volatility = 0.15  # below target → full size
    wild = buy_signal()
    wild.volatility = 0.90  # 3x the 0.30 target → ~1/3 budget
    plan_calm = rm.size_buy(calm, 1.0, make_portfolio())
    plan_wild = rm.size_buy(wild, 1.0, make_portfolio())
    assert plan_calm is not None
    assert plan_wild is None or plan_wild.quantity < plan_calm.quantity


@pytest.mark.unit
def test_size_buy_fails_open_without_volatility():
    rm = RiskManager()
    sig = buy_signal()
    assert sig.volatility is None
    baseline = rm.size_buy(buy_signal(), 1.0, make_portfolio())
    no_vol = rm.size_buy(sig, 1.0, make_portfolio())
    assert no_vol is not None
    assert no_vol.quantity == baseline.quantity


# ── drawdown brake ─────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_drawdown_brake_full_size_near_highs(fake_cache):
    rm = RiskManager()
    assert await rm.drawdown_brake_factor(make_portfolio(total_value=10000)) == 1.0
    # Small dip (5%) — still full size.
    assert await rm.drawdown_brake_factor(make_portfolio(total_value=9500)) == 1.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_drawdown_brake_shrinks_after_deep_drawdown(fake_cache):
    rm = RiskManager()
    await rm.drawdown_brake_factor(make_portfolio(total_value=10000))
    factor = await rm.drawdown_brake_factor(make_portfolio(total_value=8000))
    assert factor == settings.drawdown_size_factor
    # Recovery back above the brake threshold restores full size.
    assert await rm.drawdown_brake_factor(make_portfolio(total_value=9900)) == 1.0


@pytest.mark.unit
def test_size_buy_applies_drawdown_factor():
    rm = RiskManager()
    full = rm.size_buy(buy_signal(), 1.0, make_portfolio())
    braked = rm.size_buy(buy_signal(), 1.0, make_portfolio(), drawdown_factor=0.5)
    assert full is not None and braked is not None
    assert braked.quantity < full.quantity


# ── horizon-aware profit cap ───────────────────────────────────────────


@pytest.mark.unit
def test_profit_cap_applies_by_entry_horizon():
    assert RiskManager.profit_cap_applies("momentum_daily") is True
    assert RiskManager.profit_cap_applies(None) is True
    assert RiskManager.profit_cap_applies("ts_momentum") is False
    assert RiskManager.profit_cap_applies("low_vol_trend") is False
    assert RiskManager.profit_cap_applies("high_52w") is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_long_horizon_entry_skips_take_profit_cap(fake_cache):
    rm = RiskManager()
    await rm.set_entry_strategy("AAPL", "ts_momentum")
    pos = Position(ticker="AAPL", quantity=10, avg_price=100.0, current_price=115.0)
    plans = await rm.check_exits(make_portfolio(positions={"AAPL": pos}), contexts={})
    # +15% would normally hit the +10% cap — long-horizon entries ride on.
    assert not any("take-profit" in p.reason for p in plans)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_short_horizon_entry_keeps_take_profit_cap(fake_cache):
    rm = RiskManager()
    await rm.set_entry_strategy("AAPL", "momentum_daily")
    pos = Position(ticker="AAPL", quantity=10, avg_price=100.0, current_price=115.0)
    plans = await rm.check_exits(make_portfolio(positions={"AAPL": pos}), contexts={})
    assert any("take-profit" in p.reason for p in plans)


# ── horizon-scaled minimum holding ─────────────────────────────────────


@pytest.mark.unit
def test_min_holding_scales_with_entry_horizon():
    assert RiskManager.min_holding_hours_for(None) == settings.min_holding_hours
    assert (
        RiskManager.min_holding_hours_for("momentum_daily")
        == settings.min_holding_hours
    )
    assert RiskManager.min_holding_hours_for("ts_momentum") == 480
    assert RiskManager.min_holding_hours_for("donchian_breakout") == 120


@pytest.mark.unit
@pytest.mark.asyncio
async def test_long_horizon_entry_holds_past_flat_minimum(fake_cache):
    from datetime import timedelta

    from src.utils.timezone_utils import now_us

    rm = RiskManager()
    # Entered 5 days ago — past the flat 72h floor but well inside the
    # 480h horizon of ts_momentum.
    entered = now_us() - timedelta(hours=120)
    fake_cache.store["trading:entry_time:AAPL"] = entered.isoformat()
    await rm.set_entry_strategy("AAPL", "ts_momentum")
    assert await rm.in_min_holding("AAPL") is True
    # The same age with a short-horizon opener is already sellable.
    await rm.set_entry_strategy("AAPL", "momentum_daily")
    assert await rm.in_min_holding("AAPL") is False
