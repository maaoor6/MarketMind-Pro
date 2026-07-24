"""Trading Orchestrator — the agent that manages all agents and decides everything.

Extends :class:`~src.agents.trading_agent.TradingAgent` (reusing its lifecycle,
StockArena client, risk manager, allocator, universe scanner, context builder and
execution path) and replaces the flat decision cycle with a multi-agent one:

1. Run the :class:`~src.trading.macro_gate.MacroTimingGate` first. ``OFF`` ⇒ no
   new buys this cycle (protective exits and strategy sells still run).
2. Activate the thematic :class:`~src.trading.agents.StrategyAgent` bundles for
   the current regime (Defensive in BEAR, etc.).
3. Collect signals from every strategy (all recorded for learning), but only let
   BUYs from *active* agents compete; rank them by evidence and execute under a
   macro budget multiplier, per-agent capital caps, and a cross-agent sector cap.

The single shared StockArena portfolio is the contended resource; the Orchestrator
is the sole arbiter of it.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from src.agents.news_search_agent import NewsSearchAgent
from src.agents.quant_engine import QuantEngine
from src.agents.trading_agent import TradingAgent
from src.database.cache import cache
from src.quant.fundamentals import fetch_company_profile
from src.trading.agents import (
    active_strategy_names,
    agent_for_strategy,
    build_agents,
)
from src.trading.drift_monitor import quarantined_strategies
from src.trading.execution_tracker import ExecutionTracker
from src.trading.infra_agents import (
    NO_TIGHTENING,
    CycleContext,
    InfraDirective,
    aggregate_reports,
    build_infra_agents,
)
from src.trading.macro_gate import MacroState, MacroTimingGate
from src.trading.manual_ops import pop_pending, write_ack
from src.trading.shadow import ShadowBook
from src.trading.strategies import (
    SECTOR_ROTATION_ETFS,
    Action,
    StrategyContext,
    StrategySignal,
)
from src.utils.config import settings
from src.utils.logger import get_logger
from src.utils.timezone_utils import now_utc

logger = get_logger(__name__)

_ORCH_STATE_KEY = "trading:orchestrator:state"
_AGENT_FROZEN_KEY = "trading:agent:{name}:halted_until"
_AGENT_HWM_KEY = "trading:agent:{name}:hwm"
_FORCE_OFF_KEY = "trading:macro:force_off"  # manual /force_macro_off override
_FORCE_RECONCILE_KEY = "trading:force_reconcile"  # manual /sync_positions
_HWM_TTL = 30 * 86400
# Sector proxy for the sector-rotation ETFs (fundamentals has no sector for them).
_SECTOR_ETF_LABELS = {etf: f"SECTOR:{etf}" for etf in SECTOR_ROTATION_ETFS}


class Orchestrator(TradingAgent):
    """Multi-agent orchestrator over the single shared portfolio."""

    def __init__(self, quant: QuantEngine) -> None:
        super().__init__(quant)
        self._agents = build_agents()
        # The allocator must know every strategy the agents can emit.
        all_names = sorted({s.name for a in self._agents for s in a.strategies})
        self._strategies = [s for a in self._agents for s in a.strategies]
        from src.trading.allocator import StrategyAllocator

        self._allocator = StrategyAllocator(all_names)
        self._news = NewsSearchAgent()
        self._gate = MacroTimingGate(quant, news_agent=self._news)
        self._exec_tracker = ExecutionTracker()
        self._sector_cache: dict[str, str] = {}
        # Shadow book — walk-forward winners paper-traded before promotion.
        self._shadow = ShadowBook()
        # Infra agents (tighten-only cycle observers). Empty when disabled.
        # RiskOverseer gets the live returns + sector callables.
        self._infra_agents = (
            build_infra_agents(
                risk_returns_fn=self._ticker_returns, sector_of=self._sector_of
            )
            if settings.infra_agents_enabled
            else []
        )

    async def _record_trade(self, plan, result) -> None:
        """Persist the trade (parent) then record live-vs-decision drift."""
        await super()._record_trade(plan, result)
        if result.fill_price:
            await self._exec_tracker.record(
                plan.est_price, result.fill_price, plan.action
            )

    # ── Macro state (gate or legacy fallback) ─────────────────────────

    async def _macro_state(self, contexts: dict[str, StrategyContext]) -> MacroState:
        if settings.macro_gate_enabled:
            try:
                state = await self._gate.evaluate()
            except Exception as exc:  # noqa: BLE001
                logger.warning("macro_gate_failed_fallback", error=str(exc))
                state = await self._legacy_state(contexts)
        else:
            state = await self._legacy_state(contexts)

        # Manual admin override (/force_macro_off) always wins.
        try:
            if await cache.get(_FORCE_OFF_KEY) == "on":
                state.decision = "OFF"
                state.size_mult = 0.0
                state.rationale = f"manual force-off — {state.rationale}"
        except Exception as exc:  # noqa: BLE001
            logger.debug("force_off_check_failed", error=str(exc))
        return state

    async def _legacy_state(self, contexts: dict[str, StrategyContext]) -> MacroState:
        """Reuse the parent's SPY/VIX regime dampener (gate disabled path)."""
        factor, regime = await self._regime_state(contexts)
        return MacroState(
            regime=regime,
            decision="ON",
            size_mult=1.0,
            confidence_factor=factor,
            rationale="macro gate disabled — legacy regime dampener",
        )

    # ── Cross-sectional context injection ─────────────────────────────

    def _inject_sector_ranks(self, contexts: dict[str, StrategyContext]) -> None:
        """Rank the sector ETFs by 3-month momentum for SectorRotation."""
        rets = {
            t: ctx.signals.get("ret_3m")
            for t, ctx in contexts.items()
            if t in SECTOR_ROTATION_ETFS and ctx.signals.get("ret_3m") is not None
        }
        if len(rets) < 2:
            return
        ordered = sorted(rets, key=lambda t: rets[t])
        n = len(ordered)
        ranks = {t: (i + 1) / n for i, t in enumerate(ordered)}  # 1.0 = strongest
        for ticker in rets:
            ctx = contexts[ticker]
            payload = dict(ctx.cross_section or {})
            payload["sector_rank"] = ranks
            ctx.cross_section = payload

    # ── Per-agent freeze (Phase 4 circuit breaker) ────────────────────

    async def _frozen_agents(self) -> set[str]:
        frozen: set[str] = set()
        now = now_utc().isoformat()
        for agent in self._agents:
            until = await cache.get(_AGENT_FROZEN_KEY.format(name=agent.name))
            if until and str(until) > now:
                frozen.add(agent.name)
        return frozen

    async def _update_agent_breakers(self, portfolio) -> None:
        """Freeze an agent for N days if its holdings draw down past the limit.

        Each agent's live value is the sum of its attributed positions' market
        values (opener strategy → agent). We track a per-agent high-water mark in
        Redis; a fall of more than ``agent_breaker_dd_pct`` from it freezes only
        that agent — the others keep trading (independent of the portfolio-wide
        daily circuit breaker).
        """
        agent_value: dict[str, float] = defaultdict(float)
        for ticker, position in portfolio.positions.items():
            opener = await self._risk.get_entry_strategy(ticker)
            agent = agent_for_strategy(self._agents, opener) if opener else None
            if agent is not None:
                agent_value[agent.name] += position.market_value or (
                    position.quantity * (position.current_price or position.avg_price)
                )
        for agent in self._agents:
            value = agent_value.get(agent.name, 0.0)
            if value <= 0:
                continue
            hwm_key = _AGENT_HWM_KEY.format(name=agent.name)
            hwm = float(await cache.get(hwm_key) or 0.0)
            if value > hwm:
                await cache.set(hwm_key, value, ttl=_HWM_TTL)
                continue
            if hwm > 0 and value < hwm * (1 - settings.agent_breaker_dd_pct):
                until = (
                    now_utc() + timedelta(days=settings.agent_breaker_freeze_days)
                ).isoformat()
                await cache.set(
                    _AGENT_FROZEN_KEY.format(name=agent.name),
                    until,
                    ttl=settings.agent_breaker_freeze_days * 86400 + 3600,
                )
                logger.warning(
                    "agent_frozen",
                    agent=agent.name,
                    value=round(value, 2),
                    hwm=round(hwm, 2),
                    until=until,
                )

    # ── Manual overrides (Telegram → forced exits) ────────────────────

    async def _run_manual_ops(self, portfolio, extended: bool):
        """Execute any queued /flatten or /close requests, then ack + notify."""
        ops = await pop_pending()
        if not ops.any():
            return portfolio
        if ops.flatten:
            targets = list(portfolio.positions)
        else:
            targets = [t for t in ops.close if t in portfolio.positions]
        sold: list[str] = []
        for ticker in targets:
            position = portfolio.positions.get(ticker)
            if position is None:
                continue
            price = position.current_price or position.avg_price
            sig = StrategySignal(
                strategy="manual",
                ticker=ticker,
                action=Action.SELL,
                confidence=1.0,
                reason="manual override",
                price=price,
            )
            plan = self._risk.validate_sell(sig, portfolio)
            if plan is not None:
                portfolio = await self._execute(plan, portfolio, extended) or portfolio
                sold.append(ticker)
        kind = "flatten" if ops.flatten else "close"
        summary = f"manual {kind}: sold {sold or 'nothing (no matching positions)'}"
        await write_ack(summary)
        await self._notifier.push("reconcile", f"✅ {summary}")
        logger.info("manual_ops_applied", kind=kind, sold=sold)
        return portfolio

    # ── Infra agents (tighten-only observers) ─────────────────────────

    async def _ticker_returns(self, ticker: str) -> list[float] | None:
        """Daily returns over the correlation lookback for the RiskOverseer.

        Fail-open: any fetch error → None (the agent then adds no constraint
        for this ticker). Uses the same provider-backed price plane as the rest
        of the cycle.
        """
        if self._quant is None:
            return None
        try:
            lookback = settings.risk_corr_lookback_days
            period = "6mo" if lookback <= 126 else "1y"
            df = await self._quant.fetch_price_data(
                ticker, period=period, interval="1d"
            )
            closes = [float(c) for c in df["Close"].tail(lookback + 1).tolist()]
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "ticker_returns_failed", ticker=ticker, error=type(exc).__name__
            )
            return None
        from src.trading.risk_factors import daily_returns

        return daily_returns(closes) or None

    async def _run_infra_agents(
        self, contexts: dict[str, StrategyContext], portfolio, state: MacroState
    ) -> InfraDirective:
        """Run every infra agent read-only and aggregate their reports."""
        if not self._infra_agents:
            return NO_TIGHTENING
        cycle = CycleContext(
            contexts=contexts,
            portfolio=portfolio,
            macro=state,
            regime=state.regime,
        )
        reports = []
        for agent in self._infra_agents:
            try:
                reports.append(await agent.observe(cycle))
            except (
                Exception
            ) as exc:  # noqa: BLE001 — an agent must never break the cycle
                logger.warning(
                    "infra_agent_failed", agent=agent.name, error=type(exc).__name__
                )
        directive = aggregate_reports(reports)
        if directive.blocked or directive.size_mult < 1.0:
            logger.info(
                "infra_directive",
                blocked=sorted(directive.blocked),
                size_mult=round(directive.size_mult, 3),
            )
        return directive

    # ── Decision cycle ────────────────────────────────────────────────

    async def _trading_cycle(self, session: str) -> None:
        # Manual /sync_positions: force a reconciliation before this cycle.
        try:
            if await cache.get(_FORCE_RECONCILE_KEY) == "on":
                await cache.delete(_FORCE_RECONCILE_KEY)
                await self._reconcile_startup()
                logger.info("manual_reconcile_done")
        except Exception as exc:  # noqa: BLE001
            logger.debug("manual_reconcile_failed", error=str(exc))

        portfolio = await self._client.get_portfolio()
        extended = session != "regular"

        # Manual overrides (/flatten, /close) run before anything else and
        # bypass anti-churn — the human is explicitly de-risking.
        portfolio = await self._run_manual_ops(portfolio, extended)

        if await self._risk.circuit_breaker_tripped(portfolio):
            logger.warning("trading_halted_daily_circuit_breaker")
            return

        universe = await self._universe.get_universe(portfolio)
        tickers = [t for t in universe if t not in self._skip_tickers]
        contexts: dict[str, StrategyContext] = {}
        for ticker in tickers:
            ctx = await self._build_context(ticker, portfolio)
            if ctx is not None:
                contexts[ticker] = ctx
        self._inject_sector_ranks(contexts)

        # Shadow strategies are evaluated read-only (recorded, never executed).
        try:
            await self._shadow.observe(contexts)
        except Exception as exc:  # noqa: BLE001
            logger.debug("shadow_observe_failed", error=str(exc))

        # 1. Protective exits always run first (never gated).
        for plan in await self._risk.check_exits(portfolio, contexts):
            portfolio = await self._execute(plan, portfolio, extended) or portfolio

        # 2. Score matured signals, then determine the macro state + weights.
        await self._allocator.score_open_signals(self._lookup_price)
        state = await self._macro_state(contexts)
        weights = await self._allocator.get_weights(regime=state.regime)

        await self._update_agent_breakers(portfolio)
        frozen = await self._frozen_agents()
        active_names = {
            name
            for name in active_strategy_names(self._agents, state.regime)
            if (agent := agent_for_strategy(self._agents, name)) is None
            or agent.name not in frozen
        }
        # Auto-quarantine (drift monitor): a decayed strategy stops buying while
        # its signals keep being recorded, so it re-enables when it recovers.
        quarantined = await quarantined_strategies([s.name for s in self._strategies])
        if quarantined:
            active_names -= quarantined
            logger.info("strategies_quarantined", names=sorted(quarantined))

        # 2b. Infra agents observe the cycle → tighten-only directive.
        directive = await self._run_infra_agents(contexts, portfolio, state)

        # 3. Per-ticker decisions. Sells (from any owned strategy) run first;
        #    buys are restricted to active agents and ranked by evidence.
        sell_sigs: list[StrategySignal] = []
        buy_candidates: list[tuple[StrategySignal, float]] = []
        for ticker, ctx in contexts.items():
            if await self._risk.in_cooldown(ticker):
                continue
            decision = await self._decide_orchestrated(
                ctx, weights, state, active_names, directive
            )
            if decision is None:
                continue
            action, sig, weight_factor = decision
            if action == "sell":
                sell_sigs.append(sig)
            else:
                buy_candidates.append((sig, weight_factor))

        for sig in sell_sigs:
            plan = self._risk.validate_sell(sig, portfolio)
            if plan is not None:
                portfolio = await self._execute(plan, portfolio, extended) or portfolio

        if state.decision == "OFF":
            logger.info("macro_gate_off_skipping_buys", rationale=state.rationale)
            await self._persist_state(
                state, portfolio, frozen, buys_skipped=True, directive=directive
            )
            return

        portfolio = await self._execute_buys(
            buy_candidates, weights, portfolio, extended, state, directive
        )
        await self._persist_state(
            state, portfolio, frozen, buys_skipped=False, directive=directive
        )

    async def _decide_orchestrated(
        self,
        ctx: StrategyContext,
        weights: dict[str, float],
        state: MacroState,
        active_names: set[str],
        directive: InfraDirective | None = None,
    ) -> tuple[str, StrategySignal, float] | None:
        """Agent-aware version of TradingAgent._decide.

        All non-HOLD signals are recorded for learning. SELLs on held positions
        may come from any strategy (so an inactive agent can still exit); BUYs
        are restricted to strategies whose agent is active this regime and are
        further gated/scaled by the tighten-only infra directive.
        """
        directive = directive or NO_TIGHTENING
        candidates: list[StrategySignal] = []
        for strategy in self._strategies:
            sig = strategy.evaluate(ctx)
            if sig.action != Action.HOLD:
                await self._allocator.record_signal(sig)
                candidates.append(sig)
        if not candidates:
            return None

        enabled = [s for s in candidates if weights.get(s.strategy, 0) > 0]
        if not enabled:
            return None

        sells = [
            s
            for s in enabled
            if s.action == Action.SELL
            and ctx.position is not None
            and s.confidence >= settings.sell_confidence_gate
        ]
        if sells and not await self._risk.in_min_holding(ctx.ticker):
            best_sell = max(
                sells, key=lambda s: s.confidence * weights.get(s.strategy, 0)
            )
            return ("sell", best_sell, 0.0)

        # Infra directive: block buys on flagged tickers (sells above still run).
        if ctx.ticker in directive.blocked:
            return None

        buys = [
            s for s in enabled if s.action == Action.BUY and s.strategy in active_names
        ]
        if not buys:
            return None
        best = max(buys, key=lambda s: s.confidence * weights.get(s.strategy, 0))
        # Dampen by the macro confidence factor AND the infra per-ticker scale.
        infra_scale = directive.scale_for(ctx.ticker)
        dampened = StrategySignal(
            strategy=best.strategy,
            ticker=best.ticker,
            action=best.action,
            confidence=best.confidence * state.confidence_factor * infra_scale,
            reason=best.reason,
            price=best.price,
            volatility=best.volatility,
            atr_pct=best.atr_pct,
        )
        weight_factor = min(
            1.5, weights.get(best.strategy, 0.25) * len(self._strategies)
        )
        return ("buy", dampened, weight_factor)

    async def _execute_buys(
        self,
        buy_candidates: list[tuple[StrategySignal, float]],
        weights: dict[str, float],
        portfolio,
        extended: bool,
        state: MacroState,
        directive: InfraDirective | None = None,
    ):
        """Rank buys by evidence and execute under macro + agent + sector caps."""
        directive = directive or NO_TIGHTENING
        buy_candidates.sort(
            key=lambda pair: pair[0].confidence * weights.get(pair[0].strategy, 0),
            reverse=True,
        )
        drawdown_factor = await self._risk.drawdown_brake_factor(portfolio)
        agent_deployed = await self._agent_deployed_now(portfolio)
        sector_expo = await self._portfolio_sector_exposure(portfolio)
        total_value = max(portfolio.total_value, 1.0)

        for sig, weight_factor in buy_candidates:
            plan = self._risk.size_buy(
                sig,
                weight_factor * state.size_mult * directive.size_mult,
                portfolio,
                extended,
                drawdown_factor,
            )
            if plan is None:
                continue
            notional = plan.quantity * plan.est_price

            # Per-agent capital cap.
            agent = agent_for_strategy(self._agents, sig.strategy)
            if agent is not None:
                if (
                    agent_deployed[agent.name] + notional
                    > agent.capital_cap * total_value
                ):
                    logger.debug("agent_cap_skip", agent=agent.name, ticker=sig.ticker)
                    continue

            # Cross-agent sector-exposure cap (fail-open on unknown sector).
            sector = await self._sector_of(sig.ticker)
            if sector and sector != "UNKNOWN":
                if (
                    sector_expo.get(sector, 0.0) + notional / total_value
                    > settings.max_sector_exposure_pct
                ):
                    logger.debug("sector_cap_skip", sector=sector, ticker=sig.ticker)
                    continue

            portfolio = await self._execute(plan, portfolio, extended) or portfolio
            if agent is not None:
                agent_deployed[agent.name] += notional
            if sector and sector != "UNKNOWN":
                sector_expo[sector] = (
                    sector_expo.get(sector, 0.0) + notional / total_value
                )
        return portfolio

    # ── Sector / agent exposure helpers ───────────────────────────────

    async def _sector_of(self, ticker: str) -> str:
        if ticker in _SECTOR_ETF_LABELS:
            return _SECTOR_ETF_LABELS[ticker]
        if ticker in self._sector_cache:
            return self._sector_cache[ticker]
        try:
            profile = await fetch_company_profile(ticker)
            sector = profile.sector or "UNKNOWN"
        except Exception as exc:  # noqa: BLE001
            logger.debug("sector_lookup_failed", ticker=ticker, error=str(exc))
            sector = "UNKNOWN"
        self._sector_cache[ticker] = sector
        return sector

    async def _portfolio_sector_exposure(self, portfolio) -> dict[str, float]:
        total = max(portfolio.total_value, 1.0)
        expo: dict[str, float] = defaultdict(float)
        for ticker, position in portfolio.positions.items():
            value = position.market_value or (
                position.quantity * (position.current_price or position.avg_price)
            )
            sector = await self._sector_of(ticker)
            if sector and sector != "UNKNOWN":
                expo[sector] += value / total
        return expo

    async def _agent_deployed_now(self, portfolio) -> dict[str, float]:
        """Current $ deployed per agent, attributed via each position's opener."""
        deployed: dict[str, float] = defaultdict(float)
        for ticker, position in portfolio.positions.items():
            opener = await self._risk.get_entry_strategy(ticker)
            agent = agent_for_strategy(self._agents, opener) if opener else None
            if agent is not None:
                value = position.market_value or (
                    position.quantity * (position.current_price or position.avg_price)
                )
                deployed[agent.name] += value
        return deployed

    # ── State persistence (reporting + zero-downtime recovery) ────────

    async def _persist_state(
        self,
        state: MacroState,
        portfolio,
        frozen: set[str],
        *,
        buys_skipped: bool,
        directive: InfraDirective | None = None,
    ) -> None:
        directive = directive or NO_TIGHTENING
        snapshot = {
            "time": now_utc().isoformat(),
            "macro": state.to_dict(),
            "buys_skipped": buys_skipped,
            "frozen_agents": sorted(frozen),
            "agents": [
                {
                    "name": a.name,
                    "display": a.display,
                    "active": a.is_active(state.regime) and a.name not in frozen,
                    "strategies": sorted(a.strategy_names),
                    "capital_cap": a.capital_cap,
                }
                for a in self._agents
            ],
            "infra_agents": directive.reports,
            "infra_blocked": sorted(directive.blocked),
            "portfolio_value": portfolio.total_value,
        }
        try:
            await cache.set(_ORCH_STATE_KEY, snapshot, ttl=3600)
        except Exception as exc:  # noqa: BLE001
            logger.debug("orchestrator_state_persist_failed", error=str(exc))

    @staticmethod
    async def load_state() -> dict | None:
        return await cache.get(_ORCH_STATE_KEY)
