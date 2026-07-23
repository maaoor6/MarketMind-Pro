"""Unit tests for the thematic strategy-agent layer."""

import pytest
from src.trading.agents import (
    active_strategy_names,
    agent_for_strategy,
    build_agents,
)
from src.trading.strategies import default_strategies, experimental_strategies


@pytest.mark.unit
def test_build_agents_partitions_live_set():
    agents = build_agents()
    names = {a.name for a in agents}
    # The live 8 populate Core, Low-Vol Quality, and Defensive only. (Macro
    # Rotation goes live only if sector_rotation/cross_asset are promoted; the
    # 2026-07-23 promotion was reverted after the walk-forward regressed.)
    assert names == {"core_momentum", "low_vol_quality", "defensive"}
    # Every live strategy lands in exactly one agent.
    covered = [s for a in agents for s in a.strategy_names]
    assert sorted(covered) == sorted(s.name for s in default_strategies())
    assert len(covered) == len(set(covered))


@pytest.mark.unit
def test_build_agents_full_set_has_new_method_agents():
    agents = build_agents([*default_strategies(), *experimental_strategies()])
    names = {a.name for a in agents}
    assert "macro_rotation" in names
    assert "seasonality" in names
    macro = next(a for a in agents if a.name == "macro_rotation")
    assert macro.strategy_names == frozenset({"sector_rotation", "cross_asset"})


@pytest.mark.unit
def test_defensive_active_only_in_bear_and_volatile():
    agents = build_agents([*default_strategies(), *experimental_strategies()])
    defensive = next(a for a in agents if a.name == "defensive")
    assert defensive.is_active("BEAR")
    assert defensive.is_active("VOLATILE")
    assert not defensive.is_active("BULL")


@pytest.mark.unit
def test_active_strategy_names_by_regime():
    agents = build_agents([*default_strategies(), *experimental_strategies()])
    bear = active_strategy_names(agents, "BEAR")
    # Defensive + macro rotation active in BEAR; core momentum is not.
    assert "dip_buyer" in bear
    assert "double_seven" in bear
    assert "ts_momentum" not in bear
    bull = active_strategy_names(agents, "BULL")
    assert "ts_momentum" in bull
    assert "seasonality" in bull


@pytest.mark.unit
def test_agent_for_strategy_lookup():
    agents = build_agents([*default_strategies(), *experimental_strategies()])
    assert agent_for_strategy(agents, "ts_momentum").name == "core_momentum"
    assert agent_for_strategy(agents, "double_seven").name == "defensive"
    assert agent_for_strategy(agents, "nonexistent") is None
