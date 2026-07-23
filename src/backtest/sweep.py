"""Parameter sweep with walk-forward validation (never in-sample).

Each grid point temporarily overrides strategy class attributes and/or risk
settings, runs the full walk-forward, and reports out-of-sample CAGR /
Sharpe / max drawdown. Overrides are always restored, so the live defaults
are untouched.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from itertools import product

import pandas as pd

from src.backtest.metrics import compute_metrics
from src.backtest.walkforward import run_walk_forward
from src.trading.strategies import (
    AdxTrend,
    DipBuyer,
    FiftyTwoWeekHigh,
    GapMomentum,
    LowVolTrend,
    MeanReversion,
    MomentumDaily,
    PullbackSMA50,
    RSI2Reversion,
    VolContraction,
)
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

# param name → (kind, target object, attribute)
_PARAM_TARGETS: dict[str, tuple] = {
    "momentum_buy_score": (MomentumDaily, "buy_score"),
    "momentum_sell_score": (MomentumDaily, "sell_score"),
    "rsi_oversold": (MeanReversion, "rsi_oversold"),
    "rsi_overbought": (MeanReversion, "rsi_overbought"),
    "dip_rsi": (DipBuyer, "dip_rsi"),
    "dip_exit_rsi": (DipBuyer, "exit_rsi"),
    "gap_buy_pct": (GapMomentum, "gap_buy_pct"),
    "rsi2_buy": (RSI2Reversion, "buy_rsi2"),
    "rsi2_exit": (RSI2Reversion, "exit_rsi2"),
    "high52w_buy_proximity": (FiftyTwoWeekHigh, "buy_proximity"),
    "high52w_exit_proximity": (FiftyTwoWeekHigh, "exit_proximity"),
    "pullback_band_pct": (PullbackSMA50, "band_pct"),
    "pullback_buy_rsi_max": (PullbackSMA50, "buy_rsi_max"),
    "squeeze_max_width_pct": (VolContraction, "max_width_pct"),
    "lowvol_max_vol": (LowVolTrend, "max_vol"),
    "lowvol_exit_vol": (LowVolTrend, "exit_vol"),
    "adx_buy": (AdxTrend, "adx_buy"),
    "adx_exit": (AdxTrend, "adx_exit"),
    "stop_loss_pct": (settings, "stop_loss_pct"),
    "take_profit_pct": (settings, "take_profit_pct"),
    "trail_stop_pct": (settings, "trail_stop_pct"),
    "min_holding_hours": (settings, "min_holding_hours"),
    "sell_confidence_gate": (settings, "sell_confidence_gate"),
    "weight_sensitivity": (settings, "weight_sensitivity"),
    "target_position_vol": (settings, "target_position_vol"),
    "universe_quality_ratio": (settings, "universe_quality_ratio"),
    "drawdown_brake_pct": (settings, "drawdown_brake_pct"),
    "drawdown_size_factor": (settings, "drawdown_size_factor"),
}

# Focused default grid — small on purpose (each point is a full walk-forward).
DEFAULT_GRID: dict[str, list] = {
    "momentum_buy_score": [65, 70, 75],
    "stop_loss_pct": [0.04, 0.05, 0.07],
    "take_profit_pct": [0.10, 0.15],
}


@contextmanager
def _apply(overrides: dict[str, object]) -> Iterator[None]:
    """Set parameter overrides, always restoring the originals."""
    saved: list[tuple[object, str, object]] = []
    try:
        for name, value in overrides.items():
            target, attr = _PARAM_TARGETS[name]
            saved.append((target, attr, getattr(target, attr)))
            setattr(target, attr, value)
        yield
    finally:
        for target, attr, original in reversed(saved):
            setattr(target, attr, original)


def run_sweep(
    features: dict[str, pd.DataFrame],
    grid: dict[str, list] | None = None,
    cash: float = 10_000.0,
    vix_close: pd.Series | None = None,
    dynamic_universe: bool = True,
) -> pd.DataFrame:
    """Walk-forward every grid combination; rank by out-of-sample Sharpe."""
    grid = grid or DEFAULT_GRID
    unknown = set(grid) - set(_PARAM_TARGETS)
    if unknown:
        raise ValueError(f"unknown sweep parameters: {sorted(unknown)}")

    names = list(grid)
    rows: list[dict] = []
    for combo in product(*(grid[name] for name in names)):
        overrides = dict(zip(names, combo, strict=True))
        with _apply(overrides):
            try:
                wf = run_walk_forward(
                    features,
                    cash=cash,
                    vix_close=vix_close,
                    dynamic_universe=dynamic_universe,
                )
            except ValueError as exc:
                logger.warning(
                    "sweep_point_skipped", overrides=overrides, error=str(exc)
                )
                continue
        metrics = compute_metrics(wf.oos_equity, wf.trades)
        rows.append(
            {
                **overrides,
                "oos_cagr_pct": round(metrics.cagr_pct, 2),
                "oos_sharpe": round(metrics.sharpe, 2),
                "oos_max_dd_pct": round(metrics.max_drawdown_pct, 2),
                "trades": metrics.trade_count,
            }
        )
        logger.info(
            "sweep_point_done", overrides=overrides, sharpe=rows[-1]["oos_sharpe"]
        )

    if not rows:
        raise ValueError("no sweep points completed")
    return (
        pd.DataFrame(rows)
        .sort_values("oos_sharpe", ascending=False)
        .reset_index(drop=True)
    )
