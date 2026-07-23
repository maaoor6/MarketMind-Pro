"""CLI entrypoint: ``python -m src.backtest``.

Examples:
    python -m src.backtest                          # watchlist, all modes
    python -m src.backtest --rotate 8               # auto-pick 8 least-tested
    python -m src.backtest --tickers AAPL MSFT SPY  # explicit list
    python -m src.backtest --mode walkforward --export-weights
"""

import argparse
import sys

from src.backtest.broker import DEFAULT_SLIPPAGE_BPS
from src.backtest.runner import (
    MODES,
    run_montecarlo_pipeline,
    run_pipeline,
    run_stress_pipeline,
    run_sweep_pipeline,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m src.backtest",
        description="Offline strategy backtest — local, free, simulated money.",
    )
    parser.add_argument(
        "--mode",
        choices=(*MODES, "sweep", "stress", "montecarlo"),
        default="all",
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        metavar="TICKER",
        default=None,
        help="explicit list, or ALL for the full candidate pool (~435)",
    )
    parser.add_argument(
        "--rotate",
        type=int,
        metavar="N",
        default=None,
        help="auto-pick N least-tested tickers from the candidate pool",
    )
    parser.add_argument("--start", default=None, help="ISO date, e.g. 1996-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--cash", type=float, default=10_000.0)
    parser.add_argument("--slippage-bps", type=float, default=DEFAULT_SLIPPAGE_BPS)
    parser.add_argument("--export-weights", action="store_true")
    parser.add_argument(
        "--macro-gate",
        action="store_true",
        help="agent/all modes: add a macro-timing-gated live variant (A/B)",
    )
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument(
        "--agent-universe",
        choices=("dynamic", "full"),
        default="dynamic",
        help="agent sims: replay the live screener universe, or trade all tickers",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.mode == "sweep":
        path = run_sweep_pipeline(
            tickers=args.tickers,
            rotate=args.rotate,
            start=args.start,
            end=args.end,
            cash=args.cash,
            use_cache=not args.no_cache,
        )
        print(f"Sweep table: {path}")
        return 0
    if args.mode in ("stress", "montecarlo"):
        pipeline = (
            run_stress_pipeline if args.mode == "stress" else run_montecarlo_pipeline
        )
        print(
            pipeline(
                tickers=args.tickers,
                rotate=args.rotate,
                start=args.start,
                end=args.end,
                cash=args.cash,
                slippage_bps=args.slippage_bps,
                use_cache=not args.no_cache,
            )
        )
        return 0
    summary = run_pipeline(
        mode=args.mode,
        tickers=args.tickers,
        rotate=args.rotate,
        start=args.start,
        end=args.end,
        cash=args.cash,
        slippage_bps=args.slippage_bps,
        use_cache=not args.no_cache,
        export_weights=args.export_weights,
        agent_universe=args.agent_universe,
        macro_gate=args.macro_gate,
    )
    print()
    print(f"Backtest {summary.start} → {summary.end} on: {', '.join(summary.tickers)}")
    print()
    print(summary.text_report)
    print()
    if summary.winner:
        print(f"Winner (avg virtual return per signal): {summary.winner}")
    print(f"Report:  {summary.report_path}")
    print(f"Index:   {summary.index_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
