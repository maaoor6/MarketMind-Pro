"""Walk-forward validation — the honest test of "which strategy works".

The history is split into rolling windows: strategy weights are learned on a
training window, then the full agent trades the following test window with
those weights as its starting point. Only the stitched test-window (out-of-
sample) equity counts; in-sample results alone are biased.
"""

from dataclasses import dataclass, field

import pandas as pd

from src.backtest.allocator_sim import ScoredSignal
from src.backtest.broker import DEFAULT_SLIPPAGE_BPS, Trade
from src.backtest.engine import BacktestResult, run_full_agent
from src.backtest.features import BURN_IN_BARS
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class WalkForwardWindow:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    seed_averages: dict[str, float]
    test_return_pct: float


@dataclass
class WalkForwardResult:
    """Stitched out-of-sample performance across all test windows."""

    oos_equity: pd.Series
    windows: list[WalkForwardWindow]
    trades: list[Trade] = field(default_factory=list)
    scored: list[ScoredSignal] = field(default_factory=list)
    avg_returns: dict[str, float | None] | None = None


def _slice_with_leadin(
    feats: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp
) -> pd.DataFrame | None:
    """Window slice plus BURN_IN_BARS of lead-in so trading starts at ``start``.

    Features are precomputed on the full history, so indicator warmup is
    already settled; the lead-in only satisfies build_context's burn-in gate.
    """
    lo = int(feats.index.searchsorted(start))
    hi = int(feats.index.searchsorted(end, side="right"))
    lo_with_leadin = max(0, lo - BURN_IN_BARS)
    if hi - lo_with_leadin < BURN_IN_BARS + 20 or hi <= lo:
        return None  # not enough history for this ticker in this window
    return feats.iloc[lo_with_leadin:hi]


def _cut(series: pd.Series, start: pd.Timestamp) -> pd.Series:
    return series[series.index >= start]


def run_walk_forward(
    features: dict[str, pd.DataFrame],
    cash: float = 10_000.0,
    train_years: int = 3,
    test_years: int = 1,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    vix_close: pd.Series | None = None,
    dynamic_universe: bool = True,
    strategies: list | None = None,
) -> WalkForwardResult:
    """Roll train/test windows over the loaded history.

    Args:
        features: Precomputed feature frames (full history, SPY required).
        cash: Starting cash for every window.
        train_years / test_years: Window sizes in calendar years.

    Returns:
        WalkForwardResult with the stitched out-of-sample equity curve.
    """
    calendar = sorted({d for f in features.values() for d in f.index})
    first, last = calendar[0], calendar[-1]

    windows: list[WalkForwardWindow] = []
    oos_segments: list[pd.Series] = []
    all_trades: list[Trade] = []
    all_scored: list[ScoredSignal] = []
    last_result: BacktestResult | None = None

    train_start = first
    while True:
        train_end = train_start + pd.DateOffset(years=train_years)
        test_end = train_end + pd.DateOffset(years=test_years)
        if train_end >= last:
            break

        train_slices = {
            t: s
            for t, f in features.items()
            if (s := _slice_with_leadin(f, train_start, train_end)) is not None
        }
        test_slices = {
            t: s
            for t, f in features.items()
            if (s := _slice_with_leadin(f, train_end, min(test_end, last))) is not None
        }
        if "SPY" not in train_slices or "SPY" not in test_slices:
            train_start = train_start + pd.DateOffset(years=test_years)
            continue

        train_result = run_full_agent(
            train_slices,
            cash=cash,
            slippage_bps=slippage_bps,
            vix_close=vix_close,
            dynamic_universe=dynamic_universe,
            strategies=strategies,
        )
        seeds = {
            name: avg
            for name, avg in (train_result.avg_returns or {}).items()
            if avg is not None
        }

        test_result = run_full_agent(
            test_slices,
            cash=cash,
            slippage_bps=slippage_bps,
            vix_close=vix_close,
            dynamic_universe=dynamic_universe,
            seed_averages=seeds,
            strategies=strategies,
        )
        last_result = test_result

        segment = _cut(test_result.equity, train_end)
        if len(segment) < 2:
            train_start = train_start + pd.DateOffset(years=test_years)
            continue
        oos_segments.append(segment / float(segment.iloc[0]))
        all_trades.extend(t for t in test_result.trades if t.bar_date >= train_end)
        all_scored.extend(s for s in test_result.scored)
        windows.append(
            WalkForwardWindow(
                train_start=train_start,
                train_end=train_end,
                test_start=train_end,
                test_end=min(test_end, last),
                seed_averages=seeds,
                test_return_pct=float((segment.iloc[-1] / segment.iloc[0] - 1) * 100),
            )
        )
        logger.info(
            "walkforward_window_done",
            test_start=str(train_end.date()),
            test_return_pct=round(windows[-1].test_return_pct, 2),
        )
        # Roll forward by one test period so test windows tile the history
        # contiguously (train windows overlap; test windows never do).
        train_start = train_start + pd.DateOffset(years=test_years)

    if not oos_segments:
        raise ValueError("history too short for walk-forward windows")

    # Chain normalized segments into one continuous OOS curve starting at cash.
    stitched: list[pd.Series] = []
    level = cash
    for segment in oos_segments:
        scaled = segment * level
        stitched.append(scaled)
        level = float(scaled.iloc[-1])
    oos_equity = pd.concat(stitched)
    oos_equity = oos_equity[~oos_equity.index.duplicated(keep="last")]

    return WalkForwardResult(
        oos_equity=oos_equity,
        windows=windows,
        trades=all_trades,
        scored=all_scored,
        avg_returns=last_result.avg_returns if last_result else None,
    )
