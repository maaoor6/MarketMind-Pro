"""Pure portfolio factor/correlation math for the RiskOverseer agent.

No I/O — operates on plain return lists so it is trivially unit-tested. Used by
:class:`~src.trading.infra_agents.RiskOverseerAgent` to enforce the correlation,
beta and sector-cluster caps (all tighten-only).
"""

from __future__ import annotations


def daily_returns(closes: list[float]) -> list[float]:
    """Simple daily returns from a close series (drops the first bar)."""
    out: list[float] = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        if prev:
            out.append(closes[i] / prev - 1.0)
    return out


def _align(a: list[float], b: list[float]) -> tuple[list[float], list[float]]:
    """Trim both series to their common (most-recent) length."""
    n = min(len(a), len(b))
    return a[-n:], b[-n:]


def correlation(a: list[float], b: list[float]) -> float | None:
    """Pearson correlation of two return series, or None if undefined."""
    a, b = _align(a, b)
    n = len(a)
    if n < 2:
        return None
    ma = sum(a) / n
    mb = sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return None
    return cov / (va**0.5 * vb**0.5)


def beta(asset: list[float], market: list[float]) -> float | None:
    """OLS beta of an asset vs the market return series, or None."""
    a, m = _align(asset, market)
    n = len(m)
    if n < 2:
        return None
    ma = sum(a) / n
    mm = sum(m) / n
    cov = sum((x - ma) * (y - mm) for x, y in zip(a, m, strict=True))
    var = sum((y - mm) ** 2 for y in m)
    if var <= 0:
        return None
    return cov / var


def portfolio_beta(weights: dict[str, float], betas: dict[str, float]) -> float:
    """Value-weighted portfolio beta (missing betas default to 1.0)."""
    total = sum(weights.values())
    if total <= 0:
        return 0.0
    return sum(w * betas.get(t, 1.0) for t, w in weights.items()) / total


def max_correlation_to_held(
    candidate: list[float], held: dict[str, list[float]]
) -> float | None:
    """Highest correlation of a candidate to any held position's returns."""
    best: float | None = None
    for rets in held.values():
        c = correlation(candidate, rets)
        if c is None:
            continue
        best = c if best is None else max(best, c)
    return best
