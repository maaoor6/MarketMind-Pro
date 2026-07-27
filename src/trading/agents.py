"""Thematic strategy-agents — the multi-agent layer over the shared portfolio.

Each :class:`StrategyAgent` bundles a set of strategies that express one style,
declares which market regimes it is active in, and carries a soft capital cap.
The :class:`~src.trading.orchestrator.Orchestrator` runs the macro gate, decides
which agents are active for the current regime, collects their signals, and
arbitrates the single StockArena portfolio across them.

Grouping only — the proven per-strategy allocator keys are unchanged; agents add
regime activation, per-agent capital caps, per-agent circuit breakers, and
reporting on top. ``build_agents`` partitions whatever strategy set it is given
(the LIVE ``default_strategies()`` by default), so a new-method strategy joins
its agent automatically the moment it is promoted — nothing trades live until
then, keeping the roll-out backtest-first.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.trading.strategies import Strategy, default_strategies

# Every strategy name → its thematic role. Covers the full 24-strategy set so
# promotions land in the right agent with no extra wiring.
_ROLE_MAP: dict[str, str] = {
    # Core momentum / trend — the all-weather engine.
    "ts_momentum": "core_momentum",
    "breakout": "core_momentum",
    "trend_following": "core_momentum",
    "high_52w": "core_momentum",
    "gap_momentum": "core_momentum",
    "momentum_daily": "core_momentum",
    "macd_cross": "core_momentum",
    "adx_trend": "core_momentum",
    "supertrend": "core_momentum",
    "donchian_breakout": "core_momentum",
    "dual_momentum": "core_momentum",
    # Low-vol quality — quiet trends, pullbacks, selectivity.
    "low_vol_trend": "low_vol_quality",
    "multi_timeframe": "low_vol_quality",
    "pullback_sma50": "low_vol_quality",
    "golden_cross": "low_vol_quality",
    "vol_contraction": "low_vol_quality",
    "keltner_breakout": "low_vol_quality",
    "vol_target_trend": "low_vol_quality",
    # Defensive / mean-reversion — the BEAR specialists.
    "dip_buyer": "defensive",
    "bollinger_reversion": "defensive",
    "double_seven": "defensive",
    "mean_reversion": "defensive",
    "rsi2_reversion": "defensive",
    # Macro rotation — cross-sectional sector + intermarket (new methods).
    "sector_rotation": "macro_rotation",
    "cross_asset": "macro_rotation",
    # Seasonality — calendar tilt (new method).
    "seasonality": "seasonality",
}

# Role → display name, regimes it is active in, and soft capital cap (fraction
# of portfolio value the agent may deploy across its open positions).
_ROLE_CONFIG: dict[str, dict] = {
    "core_momentum": {
        "display": "Core Momentum",
        "regimes": frozenset({"BULL", "VOLATILE"}),
        "cap": 0.70,
    },
    "low_vol_quality": {
        "display": "Low-Vol Quality",
        "regimes": frozenset({"BULL", "VOLATILE"}),
        "cap": 0.60,
    },
    "defensive": {
        "display": "Defensive",
        "regimes": frozenset({"BEAR", "VOLATILE"}),
        "cap": 0.60,
    },
    "macro_rotation": {
        "display": "Macro Rotation",
        "regimes": frozenset({"BULL", "BEAR", "VOLATILE"}),
        "cap": 0.40,
    },
    "seasonality": {
        "display": "Seasonality",
        "regimes": frozenset({"BULL"}),
        "cap": 0.30,
    },
}

# Deterministic display / iteration order.
_ROLE_ORDER = (
    "core_momentum",
    "low_vol_quality",
    "defensive",
    "macro_rotation",
    "seasonality",
)


@dataclass
class StrategyAgent:
    """A named bundle of strategies with a regime profile and capital cap."""

    name: str
    display: str
    strategies: list[Strategy]
    active_regimes: frozenset[str]
    capital_cap: float

    def is_active(self, regime: str) -> bool:
        return regime in self.active_regimes

    @property
    def strategy_names(self) -> frozenset[str]:
        return frozenset(s.name for s in self.strategies)


def build_agents(strategies: list[Strategy] | None = None) -> list[StrategyAgent]:
    """Partition ``strategies`` (default: the live set) into thematic agents.

    Roles with no member strategies (e.g. Macro Rotation before its strategies
    are promoted) are omitted, so the live agent list only ever contains agents
    that actually have something to trade.
    """
    members = default_strategies() if strategies is None else strategies
    buckets: dict[str, list[Strategy]] = {}
    for strategy in members:
        role = _ROLE_MAP.get(strategy.name)
        if role is not None:
            buckets.setdefault(role, []).append(strategy)

    agents: list[StrategyAgent] = []
    for role in _ROLE_ORDER:
        bucket = buckets.get(role)
        if not bucket:
            continue
        cfg = _ROLE_CONFIG[role]
        agents.append(
            StrategyAgent(
                name=role,
                display=cfg["display"],
                strategies=bucket,
                active_regimes=cfg["regimes"],
                capital_cap=cfg["cap"],
            )
        )
    return agents


def agent_for_strategy(
    agents: list[StrategyAgent], strategy_name: str
) -> StrategyAgent | None:
    """Return the agent that owns ``strategy_name``, or None."""
    for agent in agents:
        if strategy_name in agent.strategy_names:
            return agent
    return None


def active_strategy_names(agents: list[StrategyAgent], regime: str) -> frozenset[str]:
    """Union of strategy names across the agents active in ``regime``."""
    names: set[str] = set()
    for agent in agents:
        if agent.is_active(regime):
            names |= agent.strategy_names
    return frozenset(names)
