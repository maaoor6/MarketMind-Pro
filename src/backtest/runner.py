"""End-to-end backtest pipeline shared by the CLI and the Telegram trigger.

``run_pipeline`` is synchronous and CPU-bound; ``run_backtest_async`` wraps it
in a thread executor so the Telegram bot's event loop never blocks.
"""

import asyncio
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from src.backtest import history as history_store
from src.backtest.allocator_sim import ScoredSignal
from src.backtest.broker import DEFAULT_SLIPPAGE_BPS
from src.backtest.data import VIX_TICKER, candidate_pool, load_universe
from src.backtest.engine import (
    BacktestResult,
    run_benchmark,
    run_full_agent,
    run_single_strategy,
)
from src.backtest.features import BURN_IN_BARS, precompute_features
from src.backtest.macro_gate_sim import build_macro_states
from src.backtest.metrics import attribute_pnl, compute_metrics, significance
from src.backtest.regimes import classify_regimes, regime_metrics
from src.backtest.report import (
    DEFAULT_REPORTS_DIR,
    render_text_report,
    write_hebrew_report,
    write_index,
)
from src.backtest.report import export_weight_seeds as _export_seeds
from src.backtest.universe_sim import daily_screen_columns
from src.backtest.validate import summarize_reports
from src.backtest.walkforward import run_walk_forward
from src.trading.strategies import default_strategies, experimental_strategies
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

MODES = ("strategies", "agent", "walkforward", "all")


@dataclass
class BacktestSummary:
    """What a completed run hands back to its caller (CLI / Telegram)."""

    report_path: Path
    index_path: Path
    winner: str | None
    text_report: str
    tickers: list[str]
    start: str
    end: str


def _strategy_stats(scored: list[ScoredSignal]) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    buckets: dict[str, list[float]] = {}
    for signal in scored:
        buckets.setdefault(signal.strategy, []).append(signal.virtual_return_pct)
    for name, values in buckets.items():
        stats[name] = {
            "avg_return_pct": sum(values) / len(values),
            "scored": len(values),
        }
    return stats


def _trim(result: BacktestResult) -> BacktestResult:
    """Drop the burn-in bars (flat cash, trading impossible) from the curve."""
    result.equity = result.equity.iloc[BURN_IN_BARS:]
    result.exposure = result.exposure.iloc[BURN_IN_BARS:]
    return result


def resolve_tickers(
    tickers: list[str] | None, rotate: int | None, history_path: Path
) -> list[str]:
    """Explicit list > rotation pick > the live agent's watchlist.

    ``ALL`` (as the sole ticker) expands to the full candidate pool (~435).
    """
    if tickers:
        resolved = [t.upper() for t in tickers]
        if resolved == ["ALL"]:
            resolved = candidate_pool()
    elif rotate:
        runs = history_store.load_runs(history_path)
        resolved = history_store.pick_rotation(candidate_pool(), rotate, runs)
    else:
        resolved = list(settings.trading_watchlist)
    if "SPY" not in resolved:
        resolved.insert(0, "SPY")
    return resolved


def _run_strategies(
    strategies: list,
    features: dict[str, pd.DataFrame],
    cash: float,
    slippage_bps: float,
    vix_close: pd.Series | None,
) -> list[BacktestResult]:
    """Run each standalone strategy, in parallel processes when possible.

    Every run is independent, so with a big universe the fork pool cuts the
    wall time to roughly 1/n_cores. Any pool failure falls back to the
    plain sequential loop (identical results).
    """

    def _sequential() -> list[BacktestResult]:
        return [
            run_single_strategy(
                s, features, cash=cash, slippage_bps=slippage_bps, vix_close=vix_close
            )
            for s in strategies
        ]

    if len(strategies) < 2 or multiprocessing.cpu_count() < 2:
        return _sequential()
    try:
        # fork shares the (large) feature frames copy-on-write; spawn would
        # re-pickle them per worker, so fall back to sequential without it.
        ctx = multiprocessing.get_context("fork")
    except ValueError:
        return _sequential()
    workers = min(len(strategies), max(1, multiprocessing.cpu_count() - 1))
    try:
        with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
            futures = [
                pool.submit(
                    run_single_strategy,
                    s,
                    features,
                    cash=cash,
                    slippage_bps=slippage_bps,
                    vix_close=vix_close,
                )
                for s in strategies
            ]
            return [f.result() for f in futures]
    except Exception as exc:  # noqa: BLE001 — any pool failure → sequential
        logger.warning("strategy_pool_failed_falling_back", error=str(exc))
        return _sequential()


def run_pipeline(
    mode: str = "all",
    tickers: list[str] | None = None,
    rotate: int | None = None,
    start: str | None = None,
    end: str | None = None,
    cash: float = 10_000.0,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    use_cache: bool = True,
    export_weights: bool = False,
    agent_universe: str = "dynamic",
    macro_gate: bool = False,
    reports_dir: Path = DEFAULT_REPORTS_DIR,
    history_path: Path = history_store.DEFAULT_HISTORY_PATH,
) -> BacktestSummary:
    """Load data, run the requested simulations, write reports and history.

    Args:
        agent_universe: "dynamic" replays the live screener universe;
            "full" lets the agent sims trade every loaded ticker.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if agent_universe not in ("dynamic", "full"):
        raise ValueError("agent_universe must be 'dynamic' or 'full'")
    dynamic = agent_universe == "dynamic"
    resolved = resolve_tickers(tickers, rotate, history_path)
    logger.info("backtest_started", mode=mode, tickers=resolved)

    quality_reports: list = []
    raw = load_universe(
        resolved + [VIX_TICKER],
        start=start,
        end=end,
        use_cache=use_cache,
        reports=quality_reports,
    )
    data_quality = summarize_reports(quality_reports)
    if data_quality["tickers_cleaned"]:
        logger.info("backtest_data_quality", **data_quality)
    vix_close = raw.pop(VIX_TICKER, pd.DataFrame()).get("Close")
    if "SPY" not in raw:
        raise ValueError("SPY history is required and could not be loaded")
    features = {
        ticker: daily_screen_columns(precompute_features(df))
        for ticker, df in raw.items()
    }
    loaded = [t for t in resolved if t in features]
    regimes = classify_regimes(features["SPY"], vix_close)

    results: list[BacktestResult] = []
    if mode in ("strategies", "all"):
        # Live set + experimental (backtest-only) strategies.
        strategies = [*default_strategies(), *experimental_strategies()]
        results.extend(
            _trim(result)
            for result in _run_strategies(
                strategies, features, cash, slippage_bps, vix_close
            )
        )
    agent_result: BacktestResult | None = None
    if mode in ("agent", "all"):
        # Research agent (all 24 strategies — keeps measuring everything) and
        # the LIVE configuration (only the promoted set) side by side; the
        # live one is the number that predicts real StockArena behavior.
        agent_result = _trim(
            run_full_agent(
                features,
                cash=cash,
                slippage_bps=slippage_bps,
                vix_close=vix_close,
                dynamic_universe=dynamic,
            )
        )
        results.append(agent_result)
        results.append(
            _trim(
                run_full_agent(
                    features,
                    cash=cash,
                    slippage_bps=slippage_bps,
                    vix_close=vix_close,
                    strategies=default_strategies(),
                    dynamic_universe=dynamic,
                    name="full_agent_live",
                )
            )
        )
        if dynamic:
            # Universe A/B: the same live configuration trading EVERY loaded
            # ticker — quantifies how much the screener universe costs/earns.
            results.append(
                _trim(
                    run_full_agent(
                        features,
                        cash=cash,
                        slippage_bps=slippage_bps,
                        vix_close=vix_close,
                        strategies=default_strategies(),
                        dynamic_universe=False,
                        name="full_agent_live_full",
                    )
                )
            )
        if macro_gate:
            # Macro-gate A/B: the live configuration with the market-timing gate
            # applied — quantifies how much the gate improves Sharpe / drawdown.
            macro_states = build_macro_states(features, vix_close)
            results.append(
                _trim(
                    run_full_agent(
                        features,
                        cash=cash,
                        slippage_bps=slippage_bps,
                        vix_close=vix_close,
                        strategies=default_strategies(),
                        dynamic_universe=dynamic,
                        name="full_agent_live_gated",
                        macro_states=macro_states,
                    )
                )
            )
    wf_scored: list[ScoredSignal] = []
    if mode in ("walkforward", "all"):
        try:
            wf = run_walk_forward(
                features,
                cash=cash,
                slippage_bps=slippage_bps,
                vix_close=vix_close,
                dynamic_universe=dynamic,
            )
            wf_scored = wf.scored
            results.append(
                BacktestResult(
                    name="walk_forward",
                    equity=wf.oos_equity,
                    exposure=pd.Series(dtype=float),
                    trades=wf.trades,
                    scored=wf.scored,
                )
            )
        except ValueError as exc:
            logger.warning("walkforward_skipped", error=str(exc))
        try:
            wf_live = run_walk_forward(
                features,
                cash=cash,
                slippage_bps=slippage_bps,
                vix_close=vix_close,
                strategies=default_strategies(),
                dynamic_universe=dynamic,
            )
            results.append(
                BacktestResult(
                    name="walk_forward_live",
                    equity=wf_live.oos_equity,
                    exposure=pd.Series(dtype=float),
                    trades=wf_live.trades,
                    scored=wf_live.scored,
                )
            )
        except ValueError as exc:
            logger.warning("walkforward_live_skipped", error=str(exc))

    if not results:
        raise ValueError("nothing was simulated — check mode and data range")

    # Benchmark over the widest simulated span.
    spans = pd.DatetimeIndex(sorted({d for r in results for d in r.equity.index}))
    spy_closes = features["SPY"].loc[features["SPY"].index.isin(spans)]
    benchmark_curve = run_benchmark(spy_closes, cash=cash)
    benchmark_metrics = compute_metrics(benchmark_curve, [])

    entries: list[dict] = []
    curves: dict[str, pd.Series] = {}
    regime_breakdown: dict[str, dict] = {}
    for result in results:
        exposure = result.exposure if not result.exposure.empty else None
        metrics = compute_metrics(result.equity, result.trades, exposure)
        _, sig_label = significance(result.trades)
        entries.append(
            {"key": result.name, "metrics": metrics, "significance": sig_label}
        )
        curves[result.name] = result.equity
        regime_breakdown[result.name] = regime_metrics(result.equity, regimes)
    curves["benchmark"] = benchmark_curve

    # Learning signal for the cumulative history: prefer walk-forward
    # (out-of-sample), fall back to the in-sample agent run.
    learning_scored = wf_scored or (agent_result.scored if agent_result else [])
    strategy_stats = _strategy_stats(learning_scored)
    regime_buckets: dict[str, dict[str, list[float]]] = {}
    for signal in learning_scored:
        if signal.regime is not None:
            regime_buckets.setdefault(signal.regime, {}).setdefault(
                signal.strategy, []
            ).append(signal.virtual_return_pct)
    regime_stats = {
        regime: {
            name: {"avg_return_pct": sum(v) / len(v), "scored": len(v)}
            for name, v in per_strategy.items()
            if v
        }
        for regime, per_strategy in regime_buckets.items()
    }
    winner = (
        max(strategy_stats, key=lambda k: strategy_stats[k]["avg_return_pct"])
        if strategy_stats
        else None
    )

    start_str = str(spans.min().date()) if len(spans) else (start or "?")
    end_str = str(spans.max().date()) if len(spans) else (end or "?")
    stamp = datetime.now(UTC).strftime("%Y-%m-%d_%H%M")
    report_path = reports_dir / f"backtest_{stamp}.html"

    runs = history_store.load_runs(history_path)
    history_agg_preview = history_store.aggregate(
        runs
        + [
            {
                "strategy_stats": strategy_stats,
                "regime_stats": regime_stats,
                "tickers": loaded,
            }
        ]
    )

    # Per-entry-strategy realized P&L inside each agent simulation — the
    # "who actually made/lost the money" attribution table.
    attribution = {
        result.name: attribute_pnl(result.trades)
        for result in results
        if result.name.startswith(("full_agent", "walk_forward")) and result.trades
    }

    summary_dict = {
        "mode": mode,
        "tickers": loaded,
        "start": start_str,
        "end": end_str,
        "cash": cash,
        "entries": entries,
        "benchmark": benchmark_metrics,
        "regime_breakdown": regime_breakdown,
        "history_agg": history_agg_preview,
        "attribution": attribution,
        "data_quality": data_quality,
    }
    write_hebrew_report(summary_dict, curves, report_path)

    history_store.append_run(
        {
            "mode": mode,
            "tickers": loaded,
            "start": start_str,
            "end": end_str,
            "cash": cash,
            "winner": winner,
            "report_file": report_path.name,
            "strategy_stats": strategy_stats,
            "regime_stats": regime_stats,
            "metrics": {
                e["key"]: {
                    "cagr_pct": round(e["metrics"].cagr_pct, 2),
                    "total_return_pct": round(e["metrics"].total_return_pct, 2),
                    "max_drawdown_pct": round(e["metrics"].max_drawdown_pct, 2),
                    "trades": e["metrics"].trade_count,
                }
                for e in entries
            },
        },
        history_path,
    )
    all_runs = history_store.load_runs(history_path)
    aggregated = history_store.aggregate(all_runs)
    index_path = write_index(all_runs, aggregated, reports_dir / "index.html")

    if export_weights and aggregated["avg_returns"]:
        _export_seeds(
            aggregated["avg_returns"],
            aggregated["regime_avg_returns"],
            period=f"{start_str}..{end_str}",
            source="cumulative_walk_forward",
            path=Path(settings.backtest_weights_path),
        )

    text = render_text_report(
        [(e["key"], e["metrics"]) for e in entries], benchmark_metrics
    )
    logger.info("backtest_finished", report=str(report_path), winner=winner)
    return BacktestSummary(
        report_path=report_path,
        index_path=index_path,
        winner=winner,
        text_report=text,
        tickers=loaded,
        start=start_str,
        end=end_str,
    )


def run_sweep_pipeline(
    tickers: list[str] | None = None,
    rotate: int | None = None,
    start: str | None = None,
    end: str | None = None,
    cash: float = 10_000.0,
    use_cache: bool = True,
    reports_dir: Path = DEFAULT_REPORTS_DIR,
    history_path: Path = history_store.DEFAULT_HISTORY_PATH,
) -> Path:
    """Load data and run the walk-forward parameter sweep; save the table."""
    from src.backtest.sweep import run_sweep

    resolved = resolve_tickers(tickers, rotate, history_path)
    raw = load_universe(
        resolved + [VIX_TICKER], start=start, end=end, use_cache=use_cache
    )
    vix_close = raw.pop(VIX_TICKER, pd.DataFrame()).get("Close")
    features = {
        ticker: daily_screen_columns(precompute_features(df))
        for ticker, df in raw.items()
    }
    table = run_sweep(features, cash=cash, vix_close=vix_close)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d_%H%M")
    path = reports_dir / f"sweep_{stamp}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(table.to_string(index=False), encoding="utf-8")
    print(table.to_string(index=False))
    best = table.iloc[0].to_dict()
    print(f"\nBest out-of-sample point: {best}")
    logger.info("sweep_finished", path=str(path))
    return path


def _load_features(
    resolved: list[str],
    start: str | None,
    end: str | None,
    use_cache: bool,
) -> tuple[dict[str, pd.DataFrame], pd.Series | None]:
    """Shared loader: OHLCV → precomputed features + the VIX close series."""
    raw = load_universe(
        resolved + [VIX_TICKER], start=start, end=end, use_cache=use_cache
    )
    vix_close = raw.pop(VIX_TICKER, pd.DataFrame()).get("Close")
    if "SPY" not in raw:
        raise ValueError("SPY history is required and could not be loaded")
    features = {
        ticker: daily_screen_columns(precompute_features(df))
        for ticker, df in raw.items()
    }
    return features, vix_close


def run_stress_pipeline(
    tickers: list[str] | None = None,
    rotate: int | None = None,
    start: str | None = None,
    end: str | None = None,
    cash: float = 10_000.0,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    use_cache: bool = True,
    history_path: Path = history_store.DEFAULT_HISTORY_PATH,
) -> str:
    """Run the crisis-window stress test (gated vs. ungated) and return text."""
    from src.backtest.stress import format_stress_report, run_stress

    resolved = resolve_tickers(tickers, rotate, history_path)
    features, vix_close = _load_features(resolved, start, end, use_cache)
    results = run_stress(features, vix_close, cash=cash, slippage_bps=slippage_bps)
    report = format_stress_report(results)
    logger.info("stress_finished")
    return report


def run_montecarlo_pipeline(
    tickers: list[str] | None = None,
    rotate: int | None = None,
    start: str | None = None,
    end: str | None = None,
    cash: float = 10_000.0,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    trials: int = 1000,
    use_cache: bool = True,
    history_path: Path = history_store.DEFAULT_HISTORY_PATH,
) -> str:
    """Bootstrap the live agent's trade sequence; return a percentile summary."""
    from src.backtest.montecarlo import run_montecarlo

    resolved = resolve_tickers(tickers, rotate, history_path)
    features, vix_close = _load_features(resolved, start, end, use_cache)
    result = _trim(
        run_full_agent(
            features,
            cash=cash,
            slippage_bps=slippage_bps,
            vix_close=vix_close,
            strategies=default_strategies(),
            name="full_agent_live",
        )
    )
    years = max((result.equity.index[-1] - result.equity.index[0]).days / 365.25, 1e-6)
    mc = run_montecarlo(result.trades, years, trials=trials)
    if mc is None:
        return "Monte Carlo: too few closed trades to resample (need ≥ 20)."
    logger.info("montecarlo_finished", trials=mc.trials)
    return (
        f"Monte Carlo — {mc.trials} bootstrap paths over {mc.trades_per_path} "
        f"live-agent trades ({years:.1f}y)\n"
        + "-" * 60
        + f"\n  CAGR   5th/50th/95th : {mc.p5_cagr:+.1f}% / "
        f"{mc.p50_cagr:+.1f}% / {mc.p95_cagr:+.1f}%"
        f"\n  maxDD  5th pctile     : {mc.p5_max_drawdown:.1f}%"
        f"\n  P(profitable)         : {mc.prob_positive:.0%}"
        f"\n\n  Verdict: {'ROBUST ✅' if mc.p5_cagr > 0 else 'FRAGILE ⚠️ (edge may be overfit)'}"
    )


async def run_backtest_async(**kwargs) -> BacktestSummary:
    """Run the pipeline off-loop (for the Telegram bot)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: run_pipeline(**kwargs))
