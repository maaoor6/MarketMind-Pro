"""Backtest runners: single-strategy comparison and full-agent simulation.

Both runners share the same bar loop: decisions are made on bar *t*'s close
and filled at bar *t+1*'s open (no lookahead), protective exits always run
before new entries, and all sizing goes through the live ``RiskManager``.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from src.backtest.allocator_sim import HORIZON_BARS, InMemoryAllocator, ScoredSignal
from src.backtest.broker import DEFAULT_SLIPPAGE_BPS, SimulatedBroker, Trade
from src.backtest.features import build_context
from src.backtest.regimes import classify_regimes
from src.backtest.risk_sim import ExitEngine, circuit_breaker_tripped
from src.backtest.universe_sim import simulate_universe
from src.trading.risk import OrderPlan, RiskManager
from src.trading.strategies import (
    STRATEGY_HORIZON_HOURS,
    Action,
    Strategy,
    StrategyContext,
    StrategySignal,
    default_strategies,
    experimental_strategies,
)
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

_VIX_THRESHOLD = 30.0


@dataclass
class BacktestResult:
    """Everything one simulation run produces."""

    name: str
    equity: pd.Series
    exposure: pd.Series
    trades: list[Trade]
    avg_returns: dict[str, float | None] | None = None
    scored: list[ScoredSignal] = field(default_factory=list)
    regimes: pd.Series | None = None


class _Simulation:
    """Shared bar-loop plumbing for both runners."""

    def __init__(
        self,
        features: dict[str, pd.DataFrame],
        cash: float,
        slippage_bps: float,
        vix_close: pd.Series | None,
    ) -> None:
        if not features:
            raise ValueError("no feature frames provided")
        self.features = features
        self.vix_close = vix_close
        self.broker = SimulatedBroker(cash, settings.trade_commission, slippage_bps)
        self.exits = ExitEngine()
        self.risk = RiskManager()
        self.calendar = sorted(
            {date for feats in features.values() for date in feats.index}
        )
        self.bar_pos: dict[str, dict[pd.Timestamp, int]] = {
            ticker: {date: i for i, date in enumerate(feats.index)}
            for ticker, feats in features.items()
        }
        self.pending: list[OrderPlan] = []
        # Bar index of each position's opening fill — drives the minimum
        # holding period for strategy SELLs (mirror of trading:entry_time).
        self.entry_bar: dict[str, int] = {}
        self.min_holding_bars = max(1, settings.min_holding_hours // 24)
        # Portfolio all-time high — drives the drawdown brake (mirror of
        # Redis trading:portfolio_hwm).
        self.portfolio_hwm: float = cash
        self.equity: list[float] = []
        self.exposure: list[float] = []
        self.dates: list[pd.Timestamp] = []

    # ── Per-bar helpers ─────────────────────────────────────────────────

    def fill_pending(self, date: pd.Timestamp, bar: int) -> None:
        """Execute queued orders at today's open; keep the rest queued."""
        remaining: list[OrderPlan] = []
        for plan in self.pending:
            pos = self.bar_pos[plan.ticker].get(date)
            if pos is None:
                remaining.append(plan)  # ticker not trading today — try later
                continue
            open_price = float(self.features[plan.ticker]["Open"].iloc[pos])
            if plan.action == "sell":
                held = self.broker.positions.get(plan.ticker)
                if held is None:
                    continue
                plan.quantity = held.quantity  # full exit of what's held now
            trade = self.broker.execute(plan, open_price, date)
            if trade is not None:
                if plan.action == "sell":
                    self.exits.clear_position_state(plan.ticker)
                    self.entry_bar.pop(plan.ticker, None)
                else:
                    self.entry_bar[plan.ticker] = bar
                    self.exits.entry_strategy[plan.ticker] = plan.strategy
        self.pending = remaining

    def sizing_kwargs(self, portfolio) -> dict:
        """Per-bar sizing extras — the drawdown brake.

        Mirrors RiskManager.drawdown_brake_factor with an in-memory HWM.
        (No min-notional relaxation: below ~$500 notional the round-trip
        commission exceeds MAX_COMMISSION_PCT anyway, so smaller trades are
        genuinely uneconomical — the brake is the real spiral protection.)
        """
        value = portfolio.total_value
        if value > self.portfolio_hwm:
            self.portfolio_hwm = value
        drawdown = (
            (self.portfolio_hwm - value) / self.portfolio_hwm
            if self.portfolio_hwm > 0
            else 0.0
        )
        factor = (
            settings.drawdown_size_factor
            if drawdown >= settings.drawdown_brake_pct
            else 1.0
        )
        return {"drawdown_factor": factor}

    def strategy_sell_allowed(self, ticker: str, bar: int) -> bool:
        """Anti-churn gate: strategy SELLs wait out the minimum holding period.

        The period scales with the OPENING strategy's horizon (mirror of
        RiskManager.min_holding_hours_for): a ts_momentum entry holds at
        least ~20 trading bars, a short-term entry keeps the 72h floor.
        Protective exits (ExitEngine) are exempt — live parity.
        """
        entered = self.entry_bar.get(ticker)
        if entered is None:
            return True
        entry_strategy = self.exits.entry_strategy.get(ticker)
        horizon_hours = STRATEGY_HORIZON_HOURS.get(entry_strategy or "", 0)
        required_bars = max(
            self.min_holding_bars,
            HORIZON_BARS.get(horizon_hours, max(1, horizon_hours // 24)),
        )
        return bar - entered >= required_bars

    def queue(self, plan: OrderPlan | None) -> None:
        if plan is not None and plan.quantity >= 1:
            self.pending.append(plan)

    def pending_tickers(self) -> set[str]:
        return {plan.ticker for plan in self.pending}

    def build_contexts(
        self, date: pd.Timestamp, tickers: list[str]
    ) -> dict[str, StrategyContext]:
        contexts: dict[str, StrategyContext] = {}
        for ticker in tickers:
            pos = self.bar_pos.get(ticker, {}).get(date)
            if pos is None:
                continue
            ctx = build_context(
                ticker,
                self.features[ticker],
                pos,
                position=self.broker.positions.get(ticker),
            )
            if ctx is not None:
                contexts[ticker] = ctx
        return contexts

    def closes_today(self, date: pd.Timestamp) -> dict[str, float]:
        prices: dict[str, float] = {}
        for ticker, positions in self.bar_pos.items():
            pos = positions.get(date)
            if pos is not None:
                prices[ticker] = float(self.features[ticker]["Close"].iloc[pos])
        return prices

    def record_day(self, portfolio) -> None:
        self.equity.append(portfolio.total_value)
        invested = portfolio.total_value - portfolio.cash
        self.exposure.append(
            invested / portfolio.total_value if portfolio.total_value > 0 else 0.0
        )

    def series(self) -> tuple[pd.Series, pd.Series]:
        index = pd.DatetimeIndex(self.dates)
        return pd.Series(self.equity, index=index), pd.Series(
            self.exposure, index=index
        )

    def vix_at(self, date: pd.Timestamp) -> float | None:
        if self.vix_close is None or self.vix_close.empty:
            return None
        window = self.vix_close[self.vix_close.index <= date]
        return float(window.iloc[-1]) if not window.empty else None

    def regime_factor(
        self, contexts: dict[str, StrategyContext], date: pd.Timestamp
    ) -> float:
        """Mirror of TradingAgent._regime_factor on historical data."""
        factor = 1.0
        spy = contexts.get("SPY")
        if spy and spy.price is not None:
            sma200 = (spy.signals.get("moving_averages") or {}).get("SMA_200")
            if sma200 is not None and spy.price < sma200:
                factor *= 0.5
        vix = self.vix_at(date)
        if vix is not None and vix > _VIX_THRESHOLD:
            factor *= 0.5
        return factor


def _run_bars(
    sim: _Simulation,
    decide: Callable,
    universe_for: Callable,
) -> None:
    """The shared calendar loop. ``decide(date, bar, contexts, portfolio)``."""
    prev_equity: float | None = None
    for bar, date in enumerate(sim.calendar):
        sim.fill_pending(date, bar)
        prices = sim.closes_today(date)
        portfolio = sim.broker.mark(prices)

        halted = prev_equity is not None and circuit_breaker_tripped(
            prev_equity, portfolio.total_value
        )

        contexts = sim.build_contexts(date, universe_for(date, portfolio))

        # Protective exits always run, halted or not (live parity).
        pending_now = sim.pending_tickers()
        for plan in sim.exits.check_exits(portfolio, contexts):
            if plan.ticker not in pending_now:
                sim.queue(plan)
                pending_now.add(plan.ticker)

        if not halted:
            decide(date, bar, contexts, portfolio, pending_now)

        sim.dates.append(date)
        sim.record_day(portfolio)
        prev_equity = portfolio.total_value


def run_single_strategy(
    strategy: Strategy,
    features: dict[str, pd.DataFrame],
    cash: float = 10_000.0,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    vix_close: pd.Series | None = None,
) -> BacktestResult:
    """Trade one strategy standalone over the loaded history."""
    sim = _Simulation(features, cash, slippage_bps, vix_close)
    risk = sim.risk

    def decide(date, bar, contexts, portfolio, pending_now) -> None:
        sizing = sim.sizing_kwargs(portfolio)
        for ticker, ctx in contexts.items():
            if ticker in pending_now:
                continue
            sig = strategy.evaluate(ctx)
            if sig.action == Action.SELL and ctx.position is not None:
                if sig.confidence >= settings.sell_confidence_gate and (
                    sim.strategy_sell_allowed(ticker, bar)
                ):
                    sim.queue(risk.validate_sell(sig, portfolio))
            elif sig.action == Action.BUY:
                sim.queue(risk.size_buy(sig, 1.0, portfolio, **sizing))

    tradable = list(features)
    _run_bars(sim, decide, lambda date, portfolio: tradable)
    equity, exposure = sim.series()
    return BacktestResult(
        name=strategy.name, equity=equity, exposure=exposure, trades=sim.broker.trades
    )


def run_full_agent(
    features: dict[str, pd.DataFrame],
    cash: float = 10_000.0,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    vix_close: pd.Series | None = None,
    dynamic_universe: bool = True,
    seed_averages: dict[str, float] | None = None,
    strategies: list[Strategy] | None = None,
    name: str = "full_agent",
    macro_states: dict | None = None,
) -> BacktestResult:
    """Replay the live agent's full decision cycle on historical data.

    By default the simulated agent evaluates the live set PLUS the
    experimental strategies — the backtest's job is to test what the bot
    *would* do with the expanded method set before any of it goes live.

    When ``macro_states`` is supplied (date → ``(decision, size_mult)`` from
    :func:`src.backtest.macro_gate_sim.build_macro_states`), the macro-timing
    gate is applied: on an ``OFF`` day no new buys are placed, otherwise each
    buy budget is scaled by ``size_mult``. Protective exits and strategy sells
    are never gated — exits always run.
    """
    if "SPY" not in features:
        raise ValueError("full-agent simulation requires SPY (regime filter)")
    sim = _Simulation(features, cash, slippage_bps, vix_close)
    if strategies is None:
        strategies = [*default_strategies(), *experimental_strategies()]
    allocator = InMemoryAllocator([s.name for s in strategies], STRATEGY_HORIZON_HOURS)
    if seed_averages:
        allocator.seed_averages(seed_averages)

    regime_labels = classify_regimes(features["SPY"], vix_close)
    tradable = list(features)

    def universe_for(date: pd.Timestamp, portfolio) -> list[str]:
        if not dynamic_universe:
            return tradable
        day_rows = {
            ticker: features[ticker].iloc[pos]
            for ticker in tradable
            if (pos := sim.bar_pos[ticker].get(date)) is not None
        }
        return simulate_universe(day_rows, portfolio)

    def decide(date, bar, contexts, portfolio, pending_now) -> None:
        prices = sim.closes_today(date)
        allocator.score_matured(bar, prices.get)
        regime = str(regime_labels.get(date, "BULL"))
        weights = allocator.weights(regime=regime)
        regime_factor = sim.regime_factor(contexts, date)

        buy_candidates: list[tuple[StrategySignal, float]] = []
        for ticker, ctx in contexts.items():
            if ticker in pending_now:
                continue
            candidates: list[StrategySignal] = []
            for strategy in strategies:
                sig = strategy.evaluate(ctx)
                if sig.action != Action.HOLD:
                    allocator.record(sig, bar, regime=regime)
                    candidates.append(sig)
            # Learning gate: disabled strategies (weight 0) never trade but
            # keep being recorded — mirrors TradingAgent._decide.
            candidates = [s for s in candidates if weights.get(s.strategy, 0) > 0]
            if not candidates:
                continue

            # Confident SELLs only, and never inside the minimum holding
            # period (anti-churn) — mirrors TradingAgent._decide.
            sells = [
                s
                for s in candidates
                if s.action == Action.SELL
                and ctx.position is not None
                and s.confidence >= settings.sell_confidence_gate
            ]
            if sells and sim.strategy_sell_allowed(ticker, bar):
                best_sell = max(
                    sells, key=lambda s: s.confidence * weights.get(s.strategy, 0)
                )
                sim.queue(sim.risk.validate_sell(best_sell, portfolio))
                continue

            buys = [s for s in candidates if s.action == Action.BUY]
            if not buys:
                continue
            best = max(buys, key=lambda s: s.confidence * weights.get(s.strategy, 0))
            dampened = StrategySignal(
                strategy=best.strategy,
                ticker=best.ticker,
                action=best.action,
                confidence=best.confidence * regime_factor,
                reason=best.reason,
                price=best.price,
                volatility=best.volatility,
            )
            weight_factor = min(1.5, weights.get(best.strategy, 0.25) * len(strategies))
            buy_candidates.append((dampened, weight_factor))

        # Macro-timing gate: OFF halts new buys (exits already queued above);
        # otherwise scale each buy budget by the day's size multiplier.
        if macro_states is not None:
            decision, size_mult = macro_states.get(date, ("ON", 1.0))
            if decision == "OFF":
                buy_candidates = []
            elif size_mult != 1.0:
                buy_candidates = [(sig, wf * size_mult) for sig, wf in buy_candidates]

        # Buys ranked by evidence — strongest ideas get the capital first.
        buy_candidates.sort(
            key=lambda pair: pair[0].confidence * weights.get(pair[0].strategy, 0),
            reverse=True,
        )
        sizing = sim.sizing_kwargs(portfolio)
        for dampened, weight_factor in buy_candidates:
            sim.queue(sim.risk.size_buy(dampened, weight_factor, portfolio, **sizing))

    _run_bars(sim, decide, universe_for)
    equity, exposure = sim.series()
    return BacktestResult(
        name=name,
        equity=equity,
        exposure=exposure,
        trades=sim.broker.trades,
        avg_returns=allocator.avg_returns(),
        scored=allocator.scored,
        regimes=regime_labels,
    )


def run_benchmark(spy: pd.DataFrame, cash: float = 10_000.0) -> pd.Series:
    """Buy-and-hold SPY equity curve on the same starting cash."""
    closes = spy["Close"]
    shares = cash / float(closes.iloc[0])
    return closes * shares
