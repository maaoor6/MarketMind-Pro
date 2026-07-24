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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.database.cache import cache
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


def build_infra_agents() -> list[InfraAgent]:
    """Construct the enabled infra agents (order = evaluation order)."""
    return [DataValidationAgent(), SentimentAgent()]
