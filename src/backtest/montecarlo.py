"""Monte-Carlo robustness check — is a strategy's edge real or overfit?

Resamples the sequence of closed-trade returns (bootstrap with replacement) to
build a *distribution* of outcomes instead of a single equity curve. A strategy
whose 5th-percentile CAGR is still positive is robust; one whose edge collapses
under resampling was fit to the specific ordering of one dataset and should not
be promoted.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from src.backtest.broker import Trade
from src.backtest.metrics import trade_returns_pct


@dataclass
class MonteCarloResult:
    trials: int
    trades_per_path: int
    p5_cagr: float
    p50_cagr: float
    p95_cagr: float
    p5_max_drawdown: float
    prob_positive: float  # fraction of paths ending above starting capital


def _path_stats(returns_pct: list[float], years: float) -> tuple[float, float]:
    """Terminal CAGR% and worst path drawdown% for one resampled sequence."""
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in returns_pct:
        equity *= 1.0 + r / 100.0
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1.0)
    cagr = (equity ** (1.0 / years) - 1.0) * 100.0 if equity > 0 else -100.0
    return cagr, max_dd * 100.0


def run_montecarlo(
    trades: list[Trade],
    years: float,
    trials: int = 1000,
    seed: int = 42,
) -> MonteCarloResult | None:
    """Bootstrap the closed-trade return sequence into an outcome distribution.

    Returns None when there are too few closed trades to resample meaningfully.
    """
    returns = trade_returns_pct(trades)
    if len(returns) < 20:
        return None
    years = max(years, 1e-6)
    rng = random.Random(seed)
    n = len(returns)

    cagrs: list[float] = []
    drawdowns: list[float] = []
    positive = 0
    for _ in range(trials):
        sample = [returns[rng.randrange(n)] for _ in range(n)]
        cagr, max_dd = _path_stats(sample, years)
        cagrs.append(cagr)
        drawdowns.append(max_dd)
        if cagr > 0:
            positive += 1

    cagrs.sort()
    drawdowns.sort()

    def _pct(values: list[float], q: float) -> float:
        idx = min(len(values) - 1, max(0, int(q * len(values))))
        return values[idx]

    return MonteCarloResult(
        trials=trials,
        trades_per_path=n,
        p5_cagr=_pct(cagrs, 0.05),
        p50_cagr=_pct(cagrs, 0.50),
        p95_cagr=_pct(cagrs, 0.95),
        p5_max_drawdown=_pct(drawdowns, 0.05),
        prob_positive=positive / trials,
    )
