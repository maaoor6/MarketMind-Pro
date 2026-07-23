"""Autonomous trading agent — decides and executes paper trades on StockArena.

Runs as a concurrent task next to QuantEngine. Every cycle it:
  1. Checks the kill-switches (env flag + Redis flag) and the live session.
  2. Fetches the portfolio and runs the daily circuit breaker.
  3. Scans the market for a dynamic ticker universe (UniverseScanner) and
     builds per-ticker contexts from QuantEngine data (3 timeframes).
  4. Executes protective exits first (stop-loss / trailing stop / Fib break).
  5. Scores matured strategy signals and refreshes adaptive weights.
  6. Picks the best strategy signal per ticker, sizes it, and executes.

All money is simulated. Fail-closed: any missing data or API error results in
no trade, and auth errors stop the agent permanently with an admin alert.
"""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal

import telegram
from sqlalchemy import select
from telegram.constants import ParseMode

from src.agents.quant_engine import QuantEngine
from src.database.cache import cache
from src.database.models import TradeRecord
from src.database.session import AsyncSessionLocal
from src.quant.fibonacci import calculate_fibonacci
from src.quant.indicators import generate_signals, momentum_score
from src.trading.allocator import StrategyAllocator
from src.trading.risk import OrderPlan, RiskManager
from src.trading.stockarena_client import (
    FatalTradingError,
    StockArenaClient,
    StockArenaError,
    TradeResult,
    TradeTimeoutError,
)
from src.trading.strategies import (
    Action,
    StrategyContext,
    StrategySignal,
    default_strategies,
)
from src.trading.universe import UniverseScanner
from src.utils.config import settings
from src.utils.logger import get_logger
from src.utils.timezone_utils import now_us, now_utc

logger = get_logger(__name__)

_ENABLED_KEY = "trading:enabled"
_LAST_DECISION_KEY = "trading:last_decision:{ticker}"
_VIX_CACHE_KEY = "trading:vix"
_TRADABLE_SESSIONS = {"regular", "pre_market", "after_hours"}


class TradingAgent:
    """Autonomous multi-strategy paper-trading agent."""

    def __init__(self, quant: QuantEngine) -> None:
        self._quant = quant
        self._client = StockArenaClient()
        self._risk = RiskManager()
        self._strategies = default_strategies()
        self._allocator = StrategyAllocator([s.name for s in self._strategies])
        self._universe = UniverseScanner()
        self._running = False
        self._fatal_error: str | None = None
        self._cycle_lock = asyncio.Lock()
        self._reconciled = False
        self._skip_tickers: set[str] = set()  # TICKER_NOT_FOUND for this session

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def run_loop(self) -> None:
        """Main loop — decide and trade every trading_interval_seconds."""
        self._running = True
        logger.info("trading_agent_started", enabled=settings.trading_enabled)
        while self._running:
            try:
                delay = await self._tick()
            except FatalTradingError as exc:
                self._fatal_error = str(exc)
                logger.error("trading_agent_fatal", error=str(exc))
                await self._notify(
                    "🛑 <b>Trading agent stopped permanently</b>\n"
                    f"Fatal API error: <code>{exc.code}</code>. "
                    "Check the StockArena token / bot status and restart."
                )
                break
            except Exception as exc:  # noqa: BLE001
                logger.error("trading_cycle_error", error=str(exc))
                delay = settings.trading_interval_seconds
            await asyncio.sleep(delay)
        logger.info("trading_agent_loop_exited")

    async def _tick(self) -> float:
        """One iteration; returns seconds to sleep before the next."""
        if not await self._is_enabled():
            return 60

        status = await self._client.market_status()
        session = str(status.get("session") or "closed")
        if not self._session_tradable(session):
            return self._sleep_until_open(status)

        if not self._reconciled:
            await self._reconcile_startup()
            self._reconciled = True

        async with self._cycle_lock:
            await self._trading_cycle(session)
        return settings.trading_interval_seconds

    def stop(self) -> None:
        """Stop the run loop."""
        self._running = False
        logger.info("trading_agent_stopped")

    async def close(self) -> None:
        """Release the HTTP client."""
        await self._client.close()

    async def health_check(self) -> dict[str, str]:
        """Standard agent health dict."""
        if self._fatal_error:
            return {"status": "error", "detail": f"fatal: {self._fatal_error}"}
        if not settings.stock_arena_token:
            return {"status": "error", "detail": "STOCK_ARENA_TOKEN not set"}
        if not settings.trading_enabled:
            return {
                "status": "ok",
                "detail": "trading disabled (TRADING_ENABLED=false)",
            }
        return await self._client.health_check()

    # ── Gating ─────────────────────────────────────────────────────────

    async def _is_enabled(self) -> bool:
        if not settings.trading_enabled or not settings.stock_arena_token:
            return False
        try:
            flag = await cache.get(_ENABLED_KEY)
        except Exception as exc:  # noqa: BLE001
            # Fail-closed: an unreadable kill-switch means no trading.
            logger.warning("trading_enabled_flag_unreadable", error=str(exc))
            return False
        return flag != "off"

    def _session_tradable(self, session: str) -> bool:
        if session == "regular":
            return True
        return session in _TRADABLE_SESSIONS and settings.trading_extended_hours

    def _sleep_until_open(self, status: dict) -> float:
        next_open = status.get("next_open_at")
        if next_open:
            try:
                dt = datetime.fromisoformat(str(next_open).replace("Z", "+00:00"))
                seconds = (dt - now_utc()).total_seconds()
                return max(60.0, min(seconds, 1800.0))
            except ValueError:
                pass
        return 900

    # ── Cycle ──────────────────────────────────────────────────────────

    async def _trading_cycle(self, session: str) -> None:
        portfolio = await self._client.get_portfolio()
        extended = session != "regular"

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

        # 1. Protective exits always run first.
        for plan in await self._risk.check_exits(portfolio, contexts):
            portfolio = await self._execute(plan, portfolio, extended) or portfolio

        # 2. Score matured signals and refresh adaptive weights.
        await self._allocator.score_open_signals(self._lookup_price)
        regime_factor, regime = await self._regime_state(contexts)
        weights = await self._allocator.get_weights(regime=regime)

        # 3. New decisions: sells first (they free cash), then buys ranked by
        #    evidence (confidence × learned weight) — the strongest ideas get
        #    the capital, not whichever ticker happens to be scanned first.
        sell_sigs: list[StrategySignal] = []
        buy_candidates: list[tuple[StrategySignal, float]] = []
        for ticker, ctx in contexts.items():
            if await self._risk.in_cooldown(ticker):
                continue
            decision = await self._decide(ctx, weights, regime_factor)
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

        buy_candidates.sort(
            key=lambda pair: pair[0].confidence * weights.get(pair[0].strategy, 0),
            reverse=True,
        )
        drawdown_factor = await self._risk.drawdown_brake_factor(portfolio)
        for sig, weight_factor in buy_candidates:
            plan = self._risk.size_buy(
                sig, weight_factor, portfolio, extended, drawdown_factor
            )
            if plan is not None:
                portfolio = await self._execute(plan, portfolio, extended) or portfolio

    async def _decide(
        self,
        ctx: StrategyContext,
        weights: dict[str, float],
        regime_factor: float,
    ) -> tuple[str, StrategySignal, float] | None:
        """Evaluate all strategies for one ticker and pick the best action.

        Returns ("sell", signal, 0.0) or ("buy", dampened_signal,
        weight_factor), or None when nothing actionable fired.
        """
        candidates: list[StrategySignal] = []
        for strategy in self._strategies:
            sig = strategy.evaluate(ctx)
            if sig.action != Action.HOLD:
                await self._allocator.record_signal(sig)
                candidates.append(sig)

        await cache.set(
            _LAST_DECISION_KEY.format(ticker=ctx.ticker),
            {
                "ticker": ctx.ticker,
                "time": now_utc().isoformat(),
                "signals": [
                    {
                        "strategy": s.strategy,
                        "action": s.action.value,
                        "confidence": round(s.confidence, 3),
                        "reason": s.reason,
                    }
                    for s in candidates
                ]
                or [{"action": "HOLD", "reason": "no strategy fired"}],
            },
            ttl=3600,
        )
        if not candidates:
            return None

        # Learning gate: signals from disabled strategies (weight 0 — proven
        # negative average) never trade; they keep being recorded above so the
        # strategy re-enables automatically if its average recovers.
        candidates = [s for s in candidates if weights.get(s.strategy, 0) > 0]
        if not candidates:
            return None

        # Risk-off bias: a SELL on a held position beats all BUYs — but only
        # a confident one, and never inside the minimum holding period (anti-
        # churn: protective exits in check_exits are exempt and already ran).
        sells = [
            s
            for s in candidates
            if s.action == Action.SELL
            and ctx.position is not None
            and s.confidence >= settings.sell_confidence_gate
        ]
        if sells and not await self._risk.in_min_holding(ctx.ticker):
            best_sell = max(
                sells, key=lambda s: s.confidence * weights.get(s.strategy, 0)
            )
            return ("sell", best_sell, 0.0)

        buys = [s for s in candidates if s.action == Action.BUY]
        if not buys:
            return None
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
        # Normalized weights sum to 1, so an equal-weight strategy would only
        # ever deploy 1/n of the budget. Rescale so equal weight → factor 1.0,
        # proven winners get more, laggards less (capped at 1.5x).
        weight_factor = min(
            1.5, weights.get(best.strategy, 0.25) * len(self._strategies)
        )
        return ("buy", dampened, weight_factor)

    # ── Context building ───────────────────────────────────────────────

    async def _build_context(self, ticker: str, portfolio) -> StrategyContext | None:
        """Assemble daily/weekly/monthly data for one ticker (fail-closed)."""
        try:
            df = await self._quant.fetch_price_data(ticker, period="1y", interval="1d")
            closes = df["Close"].squeeze()
            volumes = df["Volume"].squeeze()
            highs = df["High"].squeeze()
            lows = df["Low"].squeeze()
            signals = generate_signals(closes, volumes, high=highs, low=lows)
            try:
                live_price, prev_close = await self._quant.fetch_live_price(ticker)
                signals["price"] = live_price
                if prev_close is not None:
                    signals["prev_close"] = prev_close
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "trading_live_price_fallback", ticker=ticker, error=str(exc)
                )

            fib = calculate_fibonacci(closes, ticker=ticker)
            fib_dict = {
                "high_52w": fib.high_52w,
                "low_52w": fib.low_52w,
                "trend": fib.trend,
                "nearest_support": fib.nearest_support,
                "nearest_resistance": fib.nearest_resistance,
            }
            weekly = await self._quant.analyze_timeframe(ticker, "1wk")
            monthly = await self._quant.analyze_timeframe(ticker, "1mo")
            return StrategyContext(
                ticker=ticker,
                signals=signals,
                fibonacci=fib_dict,
                weekly=weekly,
                monthly=monthly,
                momentum=momentum_score(signals, closes),
                position=portfolio.positions.get(ticker),
                as_of=now_us().date(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("trading_context_failed", ticker=ticker, error=str(exc))
            return None

    async def _regime_state(
        self, contexts: dict[str, StrategyContext]
    ) -> tuple[float, str]:
        """BUY-confidence dampening factor + regime label (BULL/BEAR/VOLATILE).

        The label picks the matching backtest weight seeds in the allocator;
        the factor halves buy confidence per live dampener (SPY < SMA200,
        VIX > 30), exactly as before.
        """
        factor = 1.0
        regime = "BULL"
        spy = contexts.get("SPY")
        if spy and spy.price is not None:
            sma200 = (spy.signals.get("moving_averages") or {}).get("SMA_200")
            if sma200 is not None and spy.price < sma200:
                factor *= 0.5
                regime = "BEAR"
        try:
            vix = await cache.get(_VIX_CACHE_KEY)
            if vix is None:
                vix, _ = await self._quant.fetch_live_price("^VIX")
                await cache.set(_VIX_CACHE_KEY, vix, ttl=600)
            if float(vix) > 30:
                factor *= 0.5
                regime = "VOLATILE"
        except Exception as exc:  # noqa: BLE001
            # Fail-open on VIX only — the SPY filter still applies.
            logger.debug("vix_fetch_failed", error=str(exc))
        return factor, regime

    async def _lookup_price(self, ticker: str) -> float | None:
        """Price lookup for signal scoring: StockArena quote, then yfinance."""
        try:
            quote = await self._client.get_quote(ticker)
            for key in ("price", "last_price", "last", "close"):
                if quote.get(key) is not None:
                    return float(quote[key])
        except StockArenaError:
            pass
        try:
            price, _ = await self._quant.fetch_live_price(ticker)
            return price
        except Exception:  # noqa: BLE001
            return None

    # ── Execution ──────────────────────────────────────────────────────

    async def _execute(self, plan: OrderPlan, portfolio, extended: bool):
        """Validate locally, place the order, persist, notify. Returns the
        refreshed Portfolio on success, None if the order was dropped."""
        if plan.quantity < 1:
            return None
        buy_cost = plan.quantity * plan.est_price + settings.trade_commission
        if plan.action == "buy" and buy_cost > portfolio.cash:
            logger.warning("trade_dropped_insufficient_cash_local", ticker=plan.ticker)
            return None
        if plan.action == "sell":
            held = portfolio.positions.get(plan.ticker)
            if held is None or plan.quantity > held.quantity:
                logger.warning(
                    "trade_dropped_insufficient_shares_local", ticker=plan.ticker
                )
                return None

        try:
            result = await self._client.place_trade(
                plan.action, plan.ticker, plan.quantity, extended_hours=extended
            )
        except TradeTimeoutError:
            result = await self._reconcile_timed_out_order(plan)
            if result is None:
                return None
        except FatalTradingError:
            raise
        except StockArenaError as exc:
            return await self._handle_trade_error(plan, exc, extended)

        await self._record_trade(plan, result)
        await self._risk.set_cooldown(plan.ticker)
        if plan.action == "sell":
            await self._risk.clear_position_state(plan.ticker)
        else:
            await self._risk.set_entry_time(plan.ticker)
            await self._risk.set_entry_strategy(plan.ticker, plan.strategy)
        await self._notify(self._format_trade_message(plan, result))
        logger.info(
            "trade_executed",
            ticker=plan.ticker,
            action=plan.action,
            quantity=plan.quantity,
            strategy=plan.strategy,
        )
        return result.portfolio

    async def _handle_trade_error(
        self, plan: OrderPlan, exc: StockArenaError, extended: bool
    ):
        """Per-code handling for non-fatal trade rejections."""
        if exc.code == "EXTENDED_HOURS_CONFIRMATION_REQUIRED":
            if settings.trading_extended_hours and not extended:
                try:
                    result = await self._client.place_trade(
                        plan.action, plan.ticker, plan.quantity, extended_hours=True
                    )
                    await self._record_trade(plan, result)
                    await self._risk.set_cooldown(plan.ticker)
                    if plan.action == "sell":
                        await self._risk.clear_position_state(plan.ticker)
                    else:
                        await self._risk.set_entry_time(plan.ticker)
                        await self._risk.set_entry_strategy(plan.ticker, plan.strategy)
                    await self._notify(self._format_trade_message(plan, result))
                    return result.portfolio
                except StockArenaError as retry_exc:
                    logger.warning(
                        "extended_hours_retry_failed",
                        ticker=plan.ticker,
                        code=retry_exc.code,
                    )
            return None
        if exc.code == "TICKER_NOT_FOUND":
            self._skip_tickers.add(plan.ticker)
        logger.warning(
            "trade_rejected", ticker=plan.ticker, action=plan.action, code=exc.code
        )
        return None

    async def _reconcile_timed_out_order(self, plan: OrderPlan) -> TradeResult | None:
        """After a trade POST timeout, check whether it actually executed.

        Never blind-retries: a timed-out order may have filled server-side.
        """
        logger.warning("trade_timeout_reconciling", ticker=plan.ticker)
        try:
            trades = await self._client.get_trades()
        except StockArenaError:
            return None
        for trade in trades[:10]:
            if not isinstance(trade, dict):
                continue
            if (
                str(trade.get("ticker", "")).upper() == plan.ticker
                and str(trade.get("action", "")).lower() == plan.action
                and int(trade.get("quantity") or 0) == plan.quantity
                and self._is_recent(trade.get("executed_at") or trade.get("created_at"))
            ):
                logger.info("timed_out_trade_found_executed", ticker=plan.ticker)
                fill = trade.get("fill_price") or trade.get("price")
                return TradeResult(
                    ticker=plan.ticker,
                    action=plan.action,
                    quantity=plan.quantity,
                    fill_price=float(fill) if fill is not None else None,
                    portfolio=None,
                    raw=trade,
                )
        logger.info("timed_out_trade_not_executed", ticker=plan.ticker)
        return None

    @staticmethod
    def _is_recent(timestamp: object, minutes: int = 3) -> bool:
        if not timestamp:
            return False
        try:
            dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        except ValueError:
            return False
        if dt.tzinfo is None:
            return False
        return now_utc() - dt <= timedelta(minutes=minutes)

    async def _reconcile_startup(self) -> None:
        """Sync server trade history into trade_records (server is truth)."""
        try:
            trades = await self._client.get_trades()
        except StockArenaError as exc:
            logger.warning("startup_reconcile_failed", error=str(exc))
            return
        if not trades:
            return
        async with AsyncSessionLocal() as session:
            for trade in trades[:100]:
                if not isinstance(trade, dict):
                    continue
                ticker = str(trade.get("ticker", "")).upper()
                executed_at = trade.get("executed_at") or trade.get("created_at")
                if not ticker or not executed_at:
                    continue
                try:
                    dt = datetime.fromisoformat(str(executed_at).replace("Z", "+00:00"))
                except ValueError:
                    continue
                quantity = int(trade.get("quantity") or 0)
                action = str(trade.get("action", "")).upper()
                stmt = select(TradeRecord).where(
                    TradeRecord.ticker == ticker,
                    TradeRecord.executed_at == dt,
                    TradeRecord.quantity == quantity,
                    TradeRecord.action == action,
                )
                if (await session.execute(stmt)).scalar_one_or_none() is not None:
                    continue
                fill = trade.get("fill_price") or trade.get("price")
                session.add(
                    TradeRecord(
                        ticker=ticker,
                        action=action or "BUY",
                        quantity=quantity,
                        fill_price=Decimal(str(fill)) if fill is not None else None,
                        notional=(
                            Decimal(str(round(float(fill) * quantity, 6)))
                            if fill is not None
                            else None
                        ),
                        strategy="reconciled",
                        reason="imported from server trade history",
                        executed_at=dt,
                    )
                )
            await session.commit()
        logger.info("startup_reconcile_complete", server_trades=len(trades))

    async def _record_trade(self, plan: OrderPlan, result: TradeResult) -> None:
        fill = result.fill_price or plan.est_price
        async with AsyncSessionLocal() as session:
            session.add(
                TradeRecord(
                    ticker=plan.ticker,
                    action=plan.action.upper(),
                    quantity=plan.quantity,
                    fill_price=Decimal(str(round(fill, 6))),
                    notional=Decimal(str(round(fill * plan.quantity, 6))),
                    strategy=plan.strategy,
                    reason=plan.reason,
                    portfolio_value_after=(
                        Decimal(str(round(result.portfolio.total_value, 6)))
                        if result.portfolio
                        else None
                    ),
                    cash_after=(
                        Decimal(str(round(result.portfolio.cash, 6)))
                        if result.portfolio
                        else None
                    ),
                    executed_at=now_utc(),
                )
            )
            await session.commit()

    # ── Notifications ──────────────────────────────────────────────────

    def _format_trade_message(self, plan: OrderPlan, result: TradeResult) -> str:
        fill = result.fill_price or plan.est_price
        emoji = "🟢 BUY" if plan.action == "buy" else "🔴 SELL"
        lines = [
            f"{emoji} <b>{plan.ticker}</b> — {plan.quantity} shares @ ${fill:,.2f}",
            f"💵 Notional: ${fill * plan.quantity:,.2f}",
            f"🧠 Strategy: <b>{plan.strategy}</b>",
            f"📋 {plan.reason}",
        ]
        if result.portfolio:
            lines.append(
                f"📊 Portfolio: ${result.portfolio.total_value:,.2f} "
                f"(cash ${result.portfolio.cash:,.2f}, "
                f"{result.portfolio.return_pct:+.2f}%)"
            )
        return "\n".join(lines)

    async def _notify(self, text: str) -> None:
        """Send an HTML message to the admin chat (best-effort)."""
        if not settings.telegram_token or not settings.telegram_chat_id:
            return
        try:
            bot = telegram.Bot(settings.telegram_token)
            await bot.send_message(
                chat_id=settings.telegram_chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("trade_notify_failed", error=str(exc))
