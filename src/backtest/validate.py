"""Data-quality gate for historical price series.

A real audit of the cached pool found corrupt segments silently feeding every
backtest: HUBB had a 1,861-bar flat run (1977–1984, a frozen price) followed
by an 886% overnight "jump" in 1994; LNG/BRO carried similar pre-listing
garbage. Left in, a strategy "buys" the frozen price and books the artifact
jump as fake alpha.

``validate_history`` trims only the corrupt LEADING segment — everything up
to and including the last early-series corruption event — and keeps all the
good history after it (HUBB keeps 1994+, AAPL's genuine 1980s sub-$1 history
is untouched). Thresholds are calibrated to the audit: real crashes/rebounds
(2008 ≈ 20–40%, JAZZ +210% in 2009) are preserved; only clearly impossible
moves (>250%) and long frozen runs (>60 bars) are treated as corruption.
"""

from dataclasses import dataclass, field

import pandas as pd

# A flat run longer than this many bars is a frozen/illiquid data artifact,
# not real trading (60 bars ≈ 3 months of identical closes).
_FLAT_RUN_MAX = 60
# A single-bar move beyond this is an adjustment artifact — no liquid name in
# the pool moves 250%+ in a day; auto_adjust already handles real splits.
_EXTREME_JUMP = 2.5
# Only hunt for corruption in the first half of a series, so genuine recent
# volatility is never trimmed.
_EARLY_ZONE = 0.5


@dataclass
class DataQualityReport:
    ticker: str
    original_bars: int
    kept_bars: int
    trimmed_bars: int
    reasons: list[str] = field(default_factory=list)


def validate_history(
    df: pd.DataFrame, ticker: str
) -> tuple[pd.DataFrame, DataQualityReport]:
    """Trim the corrupt leading segment of a price series, if any.

    Args:
        df: Daily OHLCV frame (tz-naive DatetimeIndex), as loaded from cache.
        ticker: Symbol, for the report only.

    Returns:
        ``(clean_df, report)`` — ``clean_df`` is ``df`` with any corrupt
        prefix removed (the same object when nothing was trimmed).
    """
    n = len(df)
    if n == 0:
        return df, DataQualityReport(ticker, 0, 0, 0, ["empty"])

    close = df["Close"]
    early_cutoff = int(n * _EARLY_ZONE)
    trim_to = 0
    reasons: list[str] = []

    # (a) Frozen runs: consecutive identical closes longer than the limit.
    # Record one reason per run (at its end), not per bar.
    same = close.eq(close.shift()).to_numpy()
    run = 0
    for i in range(n):
        run = run + 1 if same[i] else 0
        run_ends = i == n - 1 or not same[i + 1]
        if run > _FLAT_RUN_MAX and run_ends and i <= early_cutoff:
            if i + 1 > trim_to:
                trim_to = i + 1
            reasons.append(f"flat_run:{run}bars_ending@{df.index[i].date()}")

    # (b) Impossible single-bar jumps in the early zone (adjustment artifacts).
    ret = close.pct_change().abs().to_numpy()
    for i in range(1, min(early_cutoff, n - 1) + 1):
        if ret[i] >= _EXTREME_JUMP and i + 1 > trim_to:
            trim_to = i + 1
            reasons.append(f"jump:{ret[i] * 100:.0f}%@{df.index[i].date()}")

    clean = df.iloc[trim_to:] if trim_to > 0 else df
    return clean, DataQualityReport(
        ticker=ticker,
        original_bars=n,
        kept_bars=len(clean),
        trimmed_bars=trim_to,
        reasons=reasons,
    )


def summarize_reports(reports: list[DataQualityReport]) -> dict:
    """Aggregate per-ticker reports for the run summary / report page."""
    cleaned = [r for r in reports if r.trimmed_bars > 0]
    cleaned.sort(key=lambda r: r.trimmed_bars, reverse=True)
    return {
        "tickers_checked": len(reports),
        "tickers_cleaned": len(cleaned),
        "bars_removed": sum(r.trimmed_bars for r in cleaned),
        "worst": [
            {
                "ticker": r.ticker,
                "trimmed_bars": r.trimmed_bars,
                "reasons": r.reasons,
            }
            for r in cleaned[:15]
        ],
    }


def audit_pool(cache_dir: str = "data/cache/backtest") -> dict:
    """Re-runnable audit over every cached parquet series (read-only).

    Usage: ``python -m src.backtest.validate``
    """
    import glob
    import os

    reports: list[DataQualityReport] = []
    for path in sorted(glob.glob(os.path.join(cache_dir, "*.parquet"))):
        ticker = os.path.basename(path).replace(".parquet", "")
        try:
            _, report = validate_history(pd.read_parquet(path), ticker)
        except Exception as exc:  # noqa: BLE001
            reports.append(DataQualityReport(ticker, 0, 0, 0, [f"unreadable:{exc}"]))
            continue
        reports.append(report)
    return summarize_reports(reports)


if __name__ == "__main__":
    summary = audit_pool()
    print(
        f"Data-quality audit: checked {summary['tickers_checked']} tickers, "
        f"cleaned {summary['tickers_cleaned']}, removed "
        f"{summary['bars_removed']:,} corrupt bars.\n"
    )
    for entry in summary["worst"]:
        print(
            f"  {entry['ticker']}: -{entry['trimmed_bars']} bars  {entry['reasons'][:3]}"
        )
