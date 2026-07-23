"""Stress testing — how the agent + macro gate behave in historical crises.

Runs the LIVE strategy set over full history twice (gate off vs. gate on) and
measures the return + max drawdown inside each crisis window. If the macro
gate + DefensiveAgent are doing their job, the gated curve should lose
materially less than the ungated one (and than SPY) through 2008 / 2020 / 2022.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.backtest.broker import DEFAULT_SLIPPAGE_BPS
from src.backtest.engine import run_benchmark, run_full_agent
from src.backtest.macro_gate_sim import build_macro_states
from src.trading.strategies import default_strategies

# Peak-to-trough crisis windows (inclusive).
CRISIS_WINDOWS: dict[str, tuple[str, str]] = {
    "GFC 2008": ("2007-10-01", "2009-06-30"),
    "COVID 2020": ("2020-02-01", "2020-06-30"),
    "Bear 2022": ("2022-01-01", "2022-12-31"),
}


@dataclass
class WindowStat:
    total_return_pct: float
    max_drawdown_pct: float


def _window_stat(equity: pd.Series, start: str, end: str) -> WindowStat | None:
    seg = equity.loc[start:end].dropna()
    if len(seg) < 2:
        return None
    total = (float(seg.iloc[-1]) - float(seg.iloc[0])) / float(seg.iloc[0]) * 100
    running_max = seg.cummax()
    max_dd = float(((seg - running_max) / running_max).min()) * 100
    return WindowStat(total_return_pct=total, max_drawdown_pct=max_dd)


def run_stress(
    features: dict[str, pd.DataFrame],
    vix_close: pd.Series | None = None,
    cash: float = 10_000.0,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    seed_averages: dict[str, float] | None = None,
) -> dict[str, dict[str, WindowStat | None]]:
    """Return per-crisis {ungated, gated, spy} window stats.

    Slippage is raised (×3) to approximate a liquidity shock during stress.
    """
    live = default_strategies()
    macro = build_macro_states(features, vix_close)
    shock_slippage = slippage_bps * 3.0

    ungated = run_full_agent(
        features,
        cash,
        shock_slippage,
        vix_close,
        seed_averages=seed_averages,
        strategies=live,
        name="live_ungated",
    )
    gated = run_full_agent(
        features,
        cash,
        shock_slippage,
        vix_close,
        seed_averages=seed_averages,
        strategies=live,
        name="live_gated",
        macro_states=macro,
    )
    spy = run_benchmark(features["SPY"], cash)

    out: dict[str, dict[str, WindowStat | None]] = {}
    for label, (start, end) in CRISIS_WINDOWS.items():
        out[label] = {
            "ungated": _window_stat(ungated.equity, start, end),
            "gated": _window_stat(gated.equity, start, end),
            "spy": _window_stat(spy, start, end),
        }
    return out


def format_stress_report(results: dict[str, dict[str, WindowStat | None]]) -> str:
    """Plain-text stress summary for the CLI."""
    lines = [
        "Stress test — crisis windows (3× slippage shock)",
        "-" * 64,
        f"{'window':<14}{'variant':<10}{'return %':>10}{'maxDD %':>10}",
    ]
    for label, variants in results.items():
        for variant in ("ungated", "gated", "spy"):
            stat = variants.get(variant)
            if stat is None:
                lines.append(f"{label:<14}{variant:<10}{'—':>10}{'—':>10}")
            else:
                lines.append(
                    f"{label:<14}{variant:<10}"
                    f"{stat.total_return_pct:>10.1f}{stat.max_drawdown_pct:>10.1f}"
                )
    return "\n".join(lines)
