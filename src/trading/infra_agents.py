"""Infrastructure agents — non-strategy agents that observe a trading cycle.

Unlike :class:`~src.trading.agents.StrategyAgent` (a bundle of signal-emitting
strategies), an :class:`InfraAgent` places **no orders**. It observes the
per-cycle bundle read-only and returns an :class:`AgentReport` that can only
*tighten* risk — block or scale down new buys, or shrink the global buy budget.
It can never widen risk or force a trade (mirrors the macro-gate overlay rule).

The Orchestrator runs the registered infra agents after building contexts and
the macro state, aggregates their reports into a single :class:`InfraDirective`,
and applies it to the buy path. Everything fails open: an agent that errors or
lacks data contributes nothing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.database.cache import cache
from src.trading.risk_factors import beta as _beta
from src.trading.risk_factors import (
    max_correlation_to_held,
    portfolio_beta,
)
from src.utils.config import settings
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.trading.macro_gate import MacroState
    from src.trading.strategies import StrategyContext

logger = get_logger(__name__)


@dataclass
class CycleContext:
    """Read-only per-cycle bundle handed to each infra agent."""

    contexts: dict[str, StrategyContext]
    portfolio: Any
    macro: MacroState
    regime: str


@dataclass
class AgentReport:
    """One infra agent's advisory findings for a cycle (tighten-only).

    Attributes:
        agent: Producer name.
        ok: False if the agent found a material problem (for display).
        block_buys: Tickers to exclude from *new* buys this cycle.
        buy_scale: Per-ticker buy-confidence multiplier in [0, 1].
        size_mult: Global buy-budget multiplier in [0, 1].
        notes: Human-readable lines for Telegram / state.
        detail: Structured payload for dashboards.
    """

    agent: str
    ok: bool = True
    block_buys: set[str] = field(default_factory=set)
    buy_scale: dict[str, float] = field(default_factory=dict)
    size_mult: float = 1.0
    notes: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)


@dataclass
class InfraDirective:
    """Aggregated, tighten-only directive applied to the buy path."""

    blocked: set[str] = field(default_factory=set)
    buy_scale: dict[str, float] = field(default_factory=dict)
    size_mult: float = 1.0
    reports: list[dict] = field(default_factory=list)

    def scale_for(self, ticker: str) -> float:
        return self.buy_scale.get(ticker, 1.0)


# Shared no-op directive (default when infra agents are disabled).
NO_TIGHTENING = InfraDirective()


def aggregate_reports(reports: list[AgentReport]) -> InfraDirective:
    """Combine reports the safe way: union blocks, min scales, product budget."""
    blocked: set[str] = set()
    buy_scale: dict[str, float] = {}
    size_mult = 1.0
    payload: list[dict] = []
    for r in reports:
        blocked |= r.block_buys
        for ticker, scale in r.buy_scale.items():
            clamped = max(0.0, min(1.0, scale))
            buy_scale[ticker] = min(buy_scale.get(ticker, 1.0), clamped)
        size_mult *= max(0.0, min(1.0, r.size_mult))
        payload.append(
            {
                "agent": r.agent,
                "ok": r.ok,
                "block_buys": sorted(r.block_buys),
                "size_mult": round(r.size_mult, 3),
                "notes": r.notes,
                "detail": r.detail,
            }
        )
    return InfraDirective(
        blocked=blocked,
        buy_scale=buy_scale,
        size_mult=max(0.0, min(1.0, size_mult)),
        reports=payload,
    )


class InfraAgent(ABC):
    """Base class for read-only, tighten-only cycle observers."""

    name: str = "infra"

    @abstractmethod
    async def observe(self, cycle: CycleContext) -> AgentReport:
        """Inspect the cycle and return a tighten-only report."""


class DataValidationAgent(InfraAgent):
    """Blocks buys on tickers flagged bad by the Phase-1 data-quality gate.

    Reads the ``data:quality:{ticker}`` verdicts written by
    ``src.data.validation`` (cross-provider consensus). Fail-open: a missing or
    unreadable verdict never blocks.
    """

    name = "data_validation"

    async def observe(self, cycle: CycleContext) -> AgentReport:
        report = AgentReport(agent=self.name)
        for ticker in cycle.contexts:
            try:
                verdict = await cache.get(f"data:quality:{ticker.upper()}")
            except Exception:  # noqa: BLE001, S112 — never break the cycle
                continue
            if isinstance(verdict, dict) and verdict.get("ok") is False:
                report.block_buys.add(ticker)
                flags = verdict.get("flags") or []
                report.notes.append(f"{ticker}: data flags {flags}")
        if report.block_buys:
            report.ok = False
            report.detail["blocked"] = sorted(report.block_buys)
        return report


class SentimentAgent(InfraAgent):
    """Scales/vetoes buys on tickers with strongly negative cached sentiment.

    Reads only the existing ``sentiment:{ticker}`` cache (populated by the news
    flows) — no new fetches, so it is cheap and fail-open. Tighten-only: a
    negative score can veto or halve a buy; positive sentiment does nothing.
    """

    name = "sentiment"

    async def observe(self, cycle: CycleContext) -> AgentReport:
        report = AgentReport(agent=self.name)
        veto = settings.sentiment_veto_score
        scale_below = settings.sentiment_scale_score
        for ticker in cycle.contexts:
            try:
                cached = await cache.get(f"sentiment:{ticker.upper()}")
            except Exception:  # noqa: BLE001, S112
                continue
            score = _extract_score(cached)
            if score is None:
                continue
            if score <= veto:
                report.block_buys.add(ticker)
                report.notes.append(f"{ticker}: sentiment veto ({score:.2f})")
            elif score <= scale_below:
                report.buy_scale[ticker] = 0.5
                report.notes.append(f"{ticker}: sentiment caution ({score:.2f})")
        if report.block_buys:
            report.ok = False
        return report


def _extract_score(cached: Any) -> float | None:
    """Pull a sentiment score out of a cached value, tolerating shapes."""
    if isinstance(cached, dict):
        val = cached.get("score")
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None
    if isinstance(cached, (int, float)):
        return float(cached)
    return None


class RiskOverseerAgent(InfraAgent):
    """Institutional factor/correlation caps — all free, tighten-only.

    Enforces three portfolio-level limits (blocks/scales new buys only):

    * **Correlation** — blocks a candidate whose return correlation to any
      current holding exceeds ``correlation_max`` (default 0.70).
    * **Beta** — if the value-weighted portfolio beta vs SPY exceeds
      ``portfolio_beta_max`` (default 1.20), shrinks the global buy budget.
    * **Sector cluster** — blocks candidates in a sector already at/above
      ``sector_cluster_max`` (default 0.30) of the book.

    Returns/sector data are pulled via injected async callables so the agent is
    pure-testable and never imports the data plane directly. Everything fails
    open — missing data contributes no constraint.
    """

    name = "risk_overseer"

    def __init__(
        self,
        fetch_returns: Callable[[str], Awaitable[list[float] | None]] | None = None,
        sector_of: Callable[[str], Awaitable[str]] | None = None,
        market_symbol: str = "SPY",
    ) -> None:
        self._fetch_returns = fetch_returns
        self._sector_of = sector_of
        self._market = market_symbol

    async def observe(self, cycle: CycleContext) -> AgentReport:
        report = AgentReport(agent=self.name)
        portfolio = cycle.portfolio
        if portfolio is None:
            return report
        positions = getattr(portfolio, "positions", {}) or {}
        held = set(positions)
        candidates = [t for t in cycle.contexts if t not in held]

        if self._fetch_returns is not None and held:
            try:
                await self._correlation_and_beta(report, positions, held, candidates)
            except Exception as exc:  # noqa: BLE001 — fail open
                logger.debug("risk_overseer_factor_failed", error=type(exc).__name__)

        if self._sector_of is not None and candidates:
            try:
                await self._sector_cluster(report, portfolio, candidates)
            except Exception as exc:  # noqa: BLE001
                logger.debug("risk_overseer_sector_failed", error=type(exc).__name__)

        if report.block_buys or report.size_mult < 1.0:
            report.ok = False
        return report

    async def _correlation_and_beta(
        self, report: AgentReport, positions, held: set[str], candidates: list[str]
    ) -> None:
        market_rets = await self._fetch_returns(self._market)
        held_rets: dict[str, list[float]] = {}
        for ticker in held:
            rets = await self._fetch_returns(ticker)
            if rets:
                held_rets[ticker] = rets

        # Portfolio beta → shrink budget if over the cap.
        if market_rets:
            betas: dict[str, float] = {}
            weights: dict[str, float] = {}
            for ticker, pos in positions.items():
                b = _beta(held_rets.get(ticker, []), market_rets)
                if b is not None:
                    betas[ticker] = b
                value = getattr(pos, "market_value", None) or (
                    getattr(pos, "quantity", 0)
                    * (
                        getattr(pos, "current_price", None)
                        or getattr(pos, "avg_price", 0)
                    )
                )
                weights[ticker] = float(value or 0.0)
            pbeta = portfolio_beta(weights, betas)
            if pbeta > settings.portfolio_beta_max > 0:
                report.size_mult = min(
                    report.size_mult, settings.portfolio_beta_max / pbeta
                )
                report.notes.append(
                    f"portfolio beta {pbeta:.2f} > {settings.portfolio_beta_max}"
                )
                report.detail["portfolio_beta"] = round(pbeta, 3)

        # Correlation → block over-correlated candidates.
        if held_rets:
            for ticker in candidates:
                rets = await self._fetch_returns(ticker)
                if not rets:
                    continue
                mc = max_correlation_to_held(rets, held_rets)
                if mc is not None and mc > settings.correlation_max:
                    report.block_buys.add(ticker)
                    report.notes.append(f"{ticker}: corr {mc:.2f} to a holding")

    async def _sector_cluster(
        self, report: AgentReport, portfolio, candidates: list[str]
    ) -> None:
        total = max(getattr(portfolio, "total_value", 0.0) or 0.0, 1.0)
        expo: dict[str, float] = {}
        for ticker, pos in (getattr(portfolio, "positions", {}) or {}).items():
            value = getattr(pos, "market_value", None) or (
                getattr(pos, "quantity", 0)
                * (getattr(pos, "current_price", None) or getattr(pos, "avg_price", 0))
            )
            sector = await self._sector_of(ticker)
            if sector and sector != "UNKNOWN":
                expo[sector] = expo.get(sector, 0.0) + float(value or 0.0) / total
        over = {s for s, e in expo.items() if e >= settings.sector_cluster_max}
        if not over:
            return
        for ticker in candidates:
            sector = await self._sector_of(ticker)
            if sector in over:
                report.block_buys.add(ticker)
                report.notes.append(f"{ticker}: sector {sector} cluster full")


def build_infra_agents(
    risk_returns_fn: Callable[[str], Awaitable[list[float] | None]] | None = None,
    sector_of: Callable[[str], Awaitable[str]] | None = None,
) -> list[InfraAgent]:
    """Construct the enabled infra agents (order = evaluation order).

    The RiskOverseer is included only when the Orchestrator supplies the
    returns + sector callables (it needs the live data plane); the pure
    DataValidation + Sentiment agents are always present.
    """
    agents: list[InfraAgent] = [DataValidationAgent(), SentimentAgent()]
    if risk_returns_fn is not None or sector_of is not None:
        agents.append(
            RiskOverseerAgent(fetch_returns=risk_returns_fn, sector_of=sector_of)
        )
    return agents
