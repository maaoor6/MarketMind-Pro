"""Historical replay of the macro-timing gate for the backtest.

Builds a per-day ``(decision, size_mult)`` series from the same quantitative
inputs the live gate scores, reusing the *identical* pure functions
:func:`src.trading.macro_gate.score_macro` / ``_decision_from_score``. Only the
backtestable inputs are populated — equity trend (SPY/QQQ vs SMA200/50),
volatility (VIX), credit trend (HYG/LQD if present), and cross-asset momentum
(GLD/USO if present). FRED-only fields (yield curve, MOVE, net liquidity) and
the live-only sentiment/blackout overlays are left neutral, exactly as the plan
documents: the quant core is backtestable, the overlays are live-only.
"""

from __future__ import annotations

import pandas as pd

from src.trading.macro_data import MacroData
from src.trading.macro_gate import _decision_from_score, score_macro


def _ratio_momentum(
    a: pd.Series, b: pd.Series, index: pd.DatetimeIndex, window: int = 21
) -> pd.Series:
    ratio = (a / b).reindex(index).ffill()
    return ratio / ratio.shift(window) - 1.0


def _momentum(close: pd.Series, index: pd.DatetimeIndex, window: int = 63) -> pd.Series:
    c = close.reindex(index).ffill()
    return c / c.shift(window) - 1.0


def build_macro_states(
    features: dict[str, pd.DataFrame],
    vix_close: pd.Series | None,
) -> dict[pd.Timestamp, tuple[str, float]]:
    """Map each trading day to ``(decision, size_mult)`` via the live gate logic."""
    if "SPY" not in features:
        return {}
    spy = features["SPY"]
    index = spy.index

    vix = (
        vix_close.reindex(index).ffill()
        if vix_close is not None
        else pd.Series(index=index, dtype=float)
    )
    qqq_close = (
        features["QQQ"]["Close"].reindex(index).ffill() if "QQQ" in features else None
    )
    qqq_sma200 = (
        features["QQQ"]["SMA_200"].reindex(index).ffill()
        if "QQQ" in features and "SMA_200" in features["QQQ"]
        else None
    )
    hyg_lqd = (
        _ratio_momentum(features["HYG"]["Close"], features["LQD"]["Close"], index)
        if "HYG" in features and "LQD" in features
        else None
    )
    gold = _momentum(features["GLD"]["Close"], index) if "GLD" in features else None
    oil = _momentum(features["USO"]["Close"], index) if "USO" in features else None

    states: dict[pd.Timestamp, tuple[str, float]] = {}
    for date in index:
        row = spy.loc[date]
        close = float(row["Close"])
        sma200 = row.get("SMA_200")
        sma50 = row.get("SMA_50")
        data = MacroData(
            spy_above_sma200=(None if pd.isna(sma200) else close > float(sma200)),
            spy_above_sma50=(None if pd.isna(sma50) else close > float(sma50)),
            qqq_above_sma200=(
                None
                if qqq_close is None
                or qqq_sma200 is None
                or pd.isna(qqq_sma200.get(date))
                else float(qqq_close.get(date)) > float(qqq_sma200.get(date))
            ),
            vix=(
                None
                if date not in vix.index or pd.isna(vix.get(date))
                else float(vix.get(date))
            ),
            hyg_lqd_trend=(
                None
                if hyg_lqd is None or pd.isna(hyg_lqd.get(date))
                else float(hyg_lqd.get(date))
            ),
            gold_trend=(
                None
                if gold is None or pd.isna(gold.get(date))
                else float(gold.get(date))
            ),
            oil_trend=(
                None if oil is None or pd.isna(oil.get(date)) else float(oil.get(date))
            ),
        )
        regime, score, _ = score_macro(data)
        states[date] = _decision_from_score(score, regime)
    return states
