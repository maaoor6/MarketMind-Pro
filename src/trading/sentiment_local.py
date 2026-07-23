"""Local, zero-cost NLP sentiment overlay for the macro gate.

Scores market-wide news headlines with a **local** model — never a paid LLM
API. Backend selection is automatic and degrades gracefully:

    FinBERT (transformers + torch, if installed)  →  VADER (vaderSentiment)  →
    a tiny built-in keyword scorer (always available).

FinBERT is loaded lazily on first use and is entirely optional — the base image
ships only VADER (pure-Python, tiny). This overlay can only *tighten* risk in
the gate; it never loosens it, so a missing/degraded backend is safe.
"""

from __future__ import annotations

import httpx

from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Minimal finance-tuned keyword fallback (used only if neither model installed).
_POSITIVE = {
    "surge",
    "soar",
    "rally",
    "beat",
    "beats",
    "gain",
    "gains",
    "record",
    "growth",
    "upgrade",
    "bullish",
    "jump",
    "rise",
    "strong",
    "profit",
    "optimism",
    "recovery",
    "outperform",
}
_NEGATIVE = {
    "plunge",
    "crash",
    "slump",
    "miss",
    "misses",
    "loss",
    "losses",
    "fall",
    "falls",
    "downgrade",
    "bearish",
    "drop",
    "weak",
    "recession",
    "fear",
    "selloff",
    "warning",
    "cut",
    "cuts",
    "default",
    "crisis",
}


class SentimentScorer:
    """Scores text to a [-1, 1] sentiment via the best available local backend."""

    def __init__(self, *, allow_finbert: bool = True) -> None:
        self._finbert = None
        self._vader = None
        self._backend = "keyword"
        if allow_finbert and self._try_load_finbert():
            self._backend = "finbert"
        elif self._try_load_vader():
            self._backend = "vader"

    @property
    def backend(self) -> str:
        return self._backend

    def _try_load_vader(self) -> bool:
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

            self._vader = SentimentIntensityAnalyzer()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("vader_unavailable", error=str(exc))
            return False

    def _try_load_finbert(self) -> bool:
        # Only probe importability here; the model itself loads lazily on first
        # score to keep startup fast and avoid the ~GB download unless used.
        try:
            import importlib.util

            if importlib.util.find_spec("transformers") is None:
                return False
            if importlib.util.find_spec("torch") is None:
                return False
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("finbert_probe_failed", error=str(exc))
            return False

    def _load_finbert_pipeline(self):
        if self._finbert is None:
            from transformers import pipeline

            self._finbert = pipeline("sentiment-analysis", model="ProsusAI/finbert")
        return self._finbert

    def score_text(self, text: str) -> float:
        if not text:
            return 0.0
        if self._backend == "finbert":
            try:
                result = self._load_finbert_pipeline()(text[:512])[0]
                label = result["label"].lower()
                score = float(result["score"])
                if label == "positive":
                    return score
                if label == "negative":
                    return -score
                return 0.0
            except Exception as exc:  # noqa: BLE001
                logger.debug("finbert_score_failed", error=str(exc))
                # Fall through to VADER/keyword on runtime failure.
        if self._backend in ("finbert", "vader") and self._vader is None:
            self._try_load_vader()
        if self._vader is not None:
            return float(self._vader.polarity_scores(text)["compound"])
        return self._keyword_score(text)

    @staticmethod
    def _keyword_score(text: str) -> float:
        words = {w.strip(".,!?:;\"'").lower() for w in text.split()}
        pos = len(words & _POSITIVE)
        neg = len(words & _NEGATIVE)
        if pos == neg == 0:
            return 0.0
        return (pos - neg) / (pos + neg)

    def score_many(self, texts: list[str]) -> float | None:
        scores = [self.score_text(t) for t in texts if t]
        if not scores:
            return None
        return sum(scores) / len(scores)


# Module-level singleton (backend detected once).
_scorer: SentimentScorer | None = None


def get_scorer() -> SentimentScorer:
    global _scorer
    if _scorer is None:
        _scorer = SentimentScorer(allow_finbert=settings.sentiment_local_enabled)
    return _scorer


async def market_sentiment(
    news_agent, tickers: tuple[str, ...] = ("SPY", "QQQ")
) -> float | None:
    """Aggregate local sentiment over market-bellwether headlines, [-1, 1].

    Reuses the existing RSS-backed :class:`NewsSearchAgent`; fail-open to None.
    """
    headlines: list[str] = []
    for ticker in tickers:
        try:
            report = await news_agent.analyze_sentiment(ticker)
            headlines.extend(report.headlines or [])
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "market_sentiment_source_failed", ticker=ticker, error=str(exc)
            )
    if not headlines:
        return None
    return get_scorer().score_many(headlines)


async def put_call_ratio() -> float | None:
    """CBOE total equity put/call ratio (fear/greed), best-effort & fail-open.

    Uses CBOE's public daily market-statistics JSON. Returns None on any error
    so the gate treats it as neutral (never blocks trading on a missing feed).
    """
    url = "https://cdn.cboe.com/api/global/us_indices/daily_market_statistics.json"
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        ratio = (data.get("data") or {}).get("ratios", {}).get("total_put_call")
        return float(ratio) if ratio is not None else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("put_call_fetch_failed", error=str(exc))
        return None
