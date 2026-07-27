"""Cumulative run history — the backtest's long-term memory.

Every run appends one record to ``data/backtest_history.json``. Weight seeds
for the live agent come from the aggregate over *all* runs (weighted by how
many signals each run scored), so each new run on different tickers sharpens
the picture instead of replacing it.
"""

import json
import random
from datetime import UTC, datetime
from pathlib import Path

from src.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_HISTORY_PATH = Path("data/backtest_history.json")


def load_runs(path: Path = DEFAULT_HISTORY_PATH) -> list[dict]:
    """All recorded runs, oldest first. Fail-open to [] on missing/corrupt."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def append_run(record: dict, path: Path = DEFAULT_HISTORY_PATH) -> None:
    """Append one run record (adds a UTC timestamp if missing)."""
    record.setdefault("generated_at", datetime.now(UTC).isoformat())
    runs = load_runs(path)
    runs.append(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(runs, fh, ensure_ascii=False, indent=1)
    logger.info("backtest_run_recorded", total_runs=len(runs))


def aggregate(runs: list[dict]) -> dict:
    """Signal-count-weighted averages per strategy (and per regime) over all runs.

    Each run record carries ``strategy_stats``:
    ``{strategy: {"avg_return_pct": float, "scored": int}}`` and optionally
    ``regime_stats``: ``{regime: {strategy: {...same...}}}``.
    """

    def _merge(buckets: dict[str, list[tuple[float, int]]]) -> dict[str, float]:
        out: dict[str, float] = {}
        for name, pairs in buckets.items():
            total_weight = sum(n for _, n in pairs)
            if total_weight > 0:
                out[name] = sum(avg * n for avg, n in pairs) / total_weight
        return out

    flat: dict[str, list[tuple[float, int]]] = {}
    per_regime: dict[str, dict[str, list[tuple[float, int]]]] = {}
    for run in runs:
        for name, stats in (run.get("strategy_stats") or {}).items():
            avg, n = stats.get("avg_return_pct"), int(stats.get("scored") or 0)
            if avg is not None and n > 0:
                flat.setdefault(name, []).append((float(avg), n))
        for regime, strategies in (run.get("regime_stats") or {}).items():
            for name, stats in strategies.items():
                avg, n = stats.get("avg_return_pct"), int(stats.get("scored") or 0)
                if avg is not None and n > 0:
                    per_regime.setdefault(regime, {}).setdefault(name, []).append(
                        (float(avg), n)
                    )

    return {
        "runs": len(runs),
        "avg_returns": _merge(flat),
        "regime_avg_returns": {r: _merge(b) for r, b in per_regime.items()},
        "signal_counts": {
            name: sum(n for _, n in pairs) for name, pairs in flat.items()
        },
    }


def ticker_coverage(runs: list[dict]) -> dict[str, int]:
    """How many runs each ticker has appeared in."""
    counts: dict[str, int] = {}
    for run in runs:
        for ticker in run.get("tickers") or []:
            counts[ticker] = counts.get(ticker, 0) + 1
    return counts


def pick_rotation(
    universe: list[str],
    n: int,
    runs: list[dict],
    seed: int = 42,
    always_include: tuple[str, ...] = ("SPY",),
) -> list[str]:
    """Choose the next run's tickers, favoring the least-tested ones.

    Deterministic for a given history length (seed + run count), so reruns
    are reproducible while consecutive runs still rotate.
    """
    rng = random.Random(seed + len(runs))
    coverage = ticker_coverage(runs)
    pool = [t for t in universe if t not in always_include]
    rng.shuffle(pool)  # random tiebreak among equally-covered tickers
    pool.sort(key=lambda t: coverage.get(t, 0))
    picked = list(always_include) + pool[: max(0, n - len(always_include))]
    return picked
