"""Market regime classification — the same signals the live agent dampens on.

BULL:     SPY above its SMA200 and VIX ≤ 30
BEAR:     SPY below its SMA200
VOLATILE: VIX > 30 (takes precedence — both live dampeners fire)
"""

import pandas as pd

from src.backtest.allocator_sim import ScoredSignal

REGIMES = ("BULL", "BEAR", "VOLATILE")

_VIX_THRESHOLD = 30.0


def classify_regimes(spy_feats: pd.DataFrame, vix_close: pd.Series | None) -> pd.Series:
    """Label every trading day with its market regime.

    Args:
        spy_feats: SPY feature frame from ``precompute_features``.
        vix_close: Daily ^VIX closes (any calendar; forward-filled), or None.

    Returns:
        Series of regime labels indexed like ``spy_feats``.
    """
    labels = pd.Series("BULL", index=spy_feats.index)
    sma200 = spy_feats["SMA_200"]
    labels[spy_feats["Close"] < sma200] = "BEAR"
    labels[sma200.isna()] = "BULL"  # warmup: no signal → no dampening (live parity)
    if vix_close is not None and not vix_close.empty:
        vix = vix_close.reindex(spy_feats.index, method="ffill")
        labels[vix > _VIX_THRESHOLD] = "VOLATILE"
    return labels


def regime_metrics(equity: pd.Series, regimes: pd.Series) -> dict[str, dict]:
    """Equity performance split by regime: total return % and day count."""
    daily = equity.pct_change().dropna()
    labels = regimes.reindex(daily.index, method="ffill")
    out: dict[str, dict] = {}
    for regime in REGIMES:
        returns = daily[labels == regime]
        if returns.empty:
            out[regime] = {"days": 0, "total_return_pct": 0.0}
            continue
        out[regime] = {
            "days": int(len(returns)),
            "total_return_pct": float(((1 + returns).prod() - 1) * 100),
        }
    return out


def regime_avg_returns(
    scored: list[ScoredSignal], strategy_names: list[str]
) -> dict[str, dict[str, float]]:
    """Average virtual return per strategy within each regime.

    Only regimes/strategies with at least one scored signal appear —
    consumers fall back to the global averages for the rest.
    """
    buckets: dict[str, dict[str, list[float]]] = {}
    for signal in scored:
        if signal.regime is None:
            continue
        buckets.setdefault(signal.regime, {}).setdefault(signal.strategy, []).append(
            signal.virtual_return_pct
        )
    return {
        regime: {
            name: sum(values) / len(values)
            for name, values in per_strategy.items()
            if name in strategy_names and values
        }
        for regime, per_strategy in buckets.items()
    }
