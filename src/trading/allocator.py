"""Adaptive strategy allocator — the bot's "which method wins" mechanism.

Every non-HOLD signal is persisted with its decision price. Once the signal's
evaluation horizon passes, the realized ("virtual") return is computed from the
live price and rolled into per-strategy aggregates. Capital weights favor
strategies with better recent virtual returns.
"""

import json
from collections.abc import Awaitable, Callable
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from src.database.cache import cache
from src.database.models import StrategyPerformance, StrategySignalRecord
from src.database.session import AsyncSessionLocal
from src.trading.strategies import STRATEGY_HORIZON_HOURS, Action, StrategySignal
from src.utils.config import settings
from src.utils.logger import get_logger
from src.utils.timezone_utils import now_utc

logger = get_logger(__name__)

_WEIGHTS_CACHE_KEY = "trading:weights:{regime}"
_WEIGHT_FLOOR = 0.1
_WEIGHT_CAP = 3.0  # safety: one hot streak / extreme seed can't dominate
_RECENT_WINDOW = 20  # signals per strategy used for weighting
_MIN_SCORED_FOR_WEIGHT = 5  # below this, strategy gets the mean weight
_SEED_CLAMP_PCT = 20.0  # safety: a corrupt/extreme seed can't distort trading


def load_weight_seeds(path: str | Path | None = None) -> dict:
    """Backtest-derived cold-start priors (fail-open to empty).

    Produced by ``python -m src.backtest --export-weights``. Values are avg
    virtual-return-pct per strategy, clamped to ±20% on the consumption side
    so a corrupt file or an extreme backtest result can't distort live sizing.
    """
    path = Path(path or settings.backtest_weights_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    def _clamp(values: object) -> dict[str, float]:
        if not isinstance(values, dict):
            return {}
        out: dict[str, float] = {}
        for name, value in values.items():
            try:
                out[str(name)] = max(
                    -_SEED_CLAMP_PCT, min(_SEED_CLAMP_PCT, float(value))
                )
            except (TypeError, ValueError):
                continue
        return out

    regime_raw = payload.get("regime_avg_returns")
    return {
        "avg_returns": _clamp(payload.get("avg_returns")),
        "regime_avg_returns": {
            str(regime): _clamp(values)
            for regime, values in (
                regime_raw.items() if isinstance(regime_raw, dict) else []
            )
        },
    }


def compute_weights(
    avg_returns: dict[str, float | None], strategy_names: list[str]
) -> dict[str, float]:
    """Evidence-sharpened weight math: amplify winners, disable proven losers.

    ``raw = 1 + weight_sensitivity × avg_return`` — per-signal averages are
    fractions of a percent, so without amplification every strategy would get
    a near-uniform weight and the "learning" would never affect trading.
    Strategies with a NEGATIVE average get weight 0 (disabled): their signals
    keep being recorded and scored, so they re-enable automatically the
    moment their average turns positive. Raw weights are floored (small
    positives survive) and capped (no single strategy dominates).

    Args:
        avg_returns: strategy -> avg virtual return pct of recent scored
            signals, or None when fewer than ``_MIN_SCORED_FOR_WEIGHT`` scored.
        strategy_names: All strategies that must receive a weight.

    Returns:
        Weights summing to 1.0.
    """
    sensitivity = settings.weight_sensitivity

    def _raw(avg: float) -> float:
        if avg < 0:
            return 0.0  # proven loser — disabled until its average recovers
        return min(_WEIGHT_CAP, max(_WEIGHT_FLOOR, 1 + sensitivity * avg / 100))

    raw: dict[str, float] = {}
    known = [_raw(v) for v in avg_returns.values() if v is not None]
    enabled = [r for r in known if r > 0]
    for name in strategy_names:
        avg = avg_returns.get(name)
        if avg is None:
            # Cold start: the mean raw weight of enabled scored strategies.
            raw[name] = sum(enabled) / len(enabled) if enabled else 1.0
        else:
            raw[name] = _raw(avg)
    total = sum(raw.values())
    if total <= 0:
        return {name: 1 / len(strategy_names) for name in strategy_names}
    return {name: value / total for name, value in raw.items()}


class StrategyAllocator:
    """Tracks virtual strategy performance and produces capital weights."""

    def __init__(self, strategy_names: list[str]) -> None:
        self._strategy_names = strategy_names

    async def record_signal(self, sig: StrategySignal) -> None:
        """Persist a non-HOLD signal for later scoring."""
        if sig.action == Action.HOLD or sig.price <= 0:
            return
        horizon = STRATEGY_HORIZON_HOURS.get(sig.strategy, 24)
        async with AsyncSessionLocal() as session:
            session.add(
                StrategySignalRecord(
                    strategy=sig.strategy,
                    ticker=sig.ticker,
                    action=sig.action.value,
                    confidence=Decimal(str(round(sig.confidence, 3))),
                    decision_price=Decimal(str(round(sig.price, 6))),
                    eval_after=now_utc() + timedelta(hours=horizon),
                    scored=False,
                )
            )
            await session.commit()

    async def score_open_signals(
        self, price_lookup: Callable[[str], Awaitable[float | None]]
    ) -> int:
        """Score matured signals against current prices. Returns count scored."""
        now = now_utc()
        scored = 0
        async with AsyncSessionLocal() as session:
            stmt = (
                select(StrategySignalRecord)
                .where(
                    StrategySignalRecord.scored.is_(False),
                    StrategySignalRecord.eval_after <= now,
                )
                .limit(50)
            )
            rows = (await session.execute(stmt)).scalars().all()
            prices: dict[str, float | None] = {}
            for row in rows:
                if row.ticker not in prices:
                    prices[row.ticker] = await price_lookup(row.ticker)
                price = prices[row.ticker]
                if price is None or price <= 0:
                    continue  # fail-closed: leave unscored, retry next cycle
                decision = float(row.decision_price)
                direction = 1.0 if row.action == "BUY" else -1.0
                row.virtual_return_pct = Decimal(
                    str(round((price - decision) / decision * 100 * direction, 4))
                )
                row.scored = True
                scored += 1
            await session.commit()

        if scored:
            await self._refresh_aggregates()
            for regime in ("BULL", "BEAR", "VOLATILE"):
                await cache.delete(_WEIGHTS_CACHE_KEY.format(regime=regime))
            logger.info("strategy_signals_scored", count=scored)
        return scored

    async def _refresh_aggregates(self) -> None:
        """Recompute strategy_performance rows from scored signals."""
        async with AsyncSessionLocal() as session:
            for name in self._strategy_names:
                stmt = (
                    select(StrategySignalRecord.virtual_return_pct)
                    .where(
                        StrategySignalRecord.strategy == name,
                        StrategySignalRecord.scored.is_(True),
                    )
                    .order_by(StrategySignalRecord.eval_after.desc())
                    .limit(_RECENT_WINDOW)
                )
                returns = [float(r) for r in (await session.execute(stmt)).scalars()]
                if not returns:
                    continue
                avg = sum(returns) / len(returns)
                wins = sum(1 for r in returns if r > 0)
                perf_stmt = select(StrategyPerformance).where(
                    StrategyPerformance.strategy == name
                )
                perf = (await session.execute(perf_stmt)).scalar_one_or_none()
                if perf is None:
                    perf = StrategyPerformance(strategy=name)
                    session.add(perf)
                perf.signals_scored = len(returns)
                perf.avg_return_pct = Decimal(str(round(avg, 4)))
                perf.win_rate = Decimal(str(round(wins / len(returns), 4)))
            await session.commit()

    async def get_performance(self) -> dict[str, dict]:
        """Per-strategy aggregates for reports: scored count, avg return, win rate."""
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(select(StrategyPerformance))).scalars().all()
            return {
                row.strategy: {
                    "signals_scored": row.signals_scored,
                    "avg_return_pct": float(row.avg_return_pct),
                    "win_rate": float(row.win_rate),
                }
                for row in rows
            }

    async def get_weights(self, regime: str = "BULL") -> dict[str, float]:
        """Current capital weights per strategy (cached 1h in Redis per regime).

        Cold-start strategies (fewer than ``_MIN_SCORED_FOR_WEIGHT`` scored
        live signals) fall back to backtest seeds for the current market
        regime instead of the flat mean — live scored data always wins once
        it exists.
        """
        cache_key = _WEIGHTS_CACHE_KEY.format(regime=regime)
        cached = await cache.get(cache_key)
        if cached and set(cached) == set(self._strategy_names):
            return {k: float(v) for k, v in cached.items()}

        seeds = load_weight_seeds()
        seed_returns: dict[str, float] = {
            **(seeds.get("avg_returns") or {}),
            **((seeds.get("regime_avg_returns") or {}).get(regime) or {}),
        }

        avg_returns: dict[str, float | None] = {}
        perf = await self.get_performance()
        for name in self._strategy_names:
            stats = perf.get(name)
            if stats and stats["signals_scored"] >= _MIN_SCORED_FOR_WEIGHT:
                avg_returns[name] = stats["avg_return_pct"]
            else:
                avg_returns[name] = seed_returns.get(name)

        weights = compute_weights(avg_returns, self._strategy_names)
        await cache.set(cache_key, weights, ttl=3600)
        return weights
