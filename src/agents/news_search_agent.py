"""News Search Agent — Google Search MCP-powered sentiment scanner."""

import asyncio
import re
from dataclasses import dataclass, field

import httpx

from src.database.cache import cache
from src.utils.config import settings
from src.utils.logger import get_logger
from src.utils.timezone_utils import now_utc

logger = get_logger(__name__)

# News sources to scan (Hebrew + English financial media)
NEWS_SOURCES = {
    "he": ["globes.co.il", "bizportal.co.il", "calcalist.co.il", "themarker.com"],
    "en": ["cnbc.com", "reuters.com", "bloomberg.com", "marketwatch.com"],
}

# Hebrew sentiment keywords
POSITIVE_HE = ["עלייה", "רווח", "צמיחה", "חיובי", "שיא", "תשואה", "ביקוש"]
NEGATIVE_HE = ["ירידה", "הפסד", "משבר", "שלילי", "תשקיף", "קנס", "חקירה"]

# English sentiment keywords
POSITIVE_EN = [
    "surge",
    "gains",
    "profit",
    "growth",
    "record",
    "bullish",
    "beat",
    "upgrade",
]
NEGATIVE_EN = [
    "drop",
    "loss",
    "crisis",
    "bearish",
    "miss",
    "downgrade",
    "investigation",
    "fine",
]


@dataclass
class SentimentReport:
    ticker: str
    timestamp: str
    score: float  # -1.0 (very negative) to +1.0 (very positive)
    headline_count: int
    sources: list[str] = field(default_factory=list)
    headlines_he: list[str] = field(default_factory=list)
    headlines_en: list[str] = field(default_factory=list)
    summary_he: str = ""
    summary_en: str = ""
    emoji: str = "⚪"

    def __post_init__(self) -> None:
        if self.score >= 0.3:
            self.emoji = "🟢"
        elif self.score <= -0.3:
            self.emoji = "🔴"
        else:
            self.emoji = "🟡"


class NewsSearchAgent:
    """Autonomous agent for news sentiment analysis via Google Search MCP."""

    def __init__(self) -> None:
        self._mcp_base_url = f"http://localhost:{settings.google_search_mcp_port}"

    async def _search_google_mcp(self, query: str, num_results: int = 10) -> list[dict]:
        """Call Google Search MCP server to get search results.

        Falls back to direct Google Custom Search API if MCP unavailable.
        """
        # Try MCP server first
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    f"{self._mcp_base_url}/tools/search_web",
                    json={"query": query, "num_results": num_results},
                )
                response.raise_for_status()
                return response.json().get("results", [])
        except httpx.ConnectError:
            logger.warning("mcp_unavailable_using_fallback", query=query)

        # Fallback: Google Custom Search JSON API
        return await self._google_custom_search_fallback(query, num_results)

    async def _google_custom_search_fallback(
        self, query: str, num_results: int = 10
    ) -> list[dict]:
        """Direct Google Custom Search API call."""
        api_key = settings.google_api_key
        cx = settings.google_search_engine_id
        if not api_key or not cx:
            logger.warning("no_google_api_credentials")
            return []

        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": api_key,
            "cx": cx,
            "q": query,
            "num": min(num_results, 10),
        }
        async with httpx.AsyncClient(timeout=15) as client:
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
                items = response.json().get("items", [])
                return [
                    {
                        "title": item.get("title", ""),
                        "url": item.get("link", ""),
                        "snippet": item.get("snippet", ""),
                    }
                    for item in items
                ]
            except Exception as exc:
                logger.error("google_search_failed", error=str(exc))
                return []

    def _score_headline(self, text: str) -> float:
        """Score a headline from -1.0 to +1.0 based on keyword matching."""
        text_lower = text.lower()
        score = 0.0
        total = 0

        for kw in POSITIVE_EN + POSITIVE_HE:
            if kw.lower() in text_lower:
                score += 1
                total += 1

        for kw in NEGATIVE_EN + NEGATIVE_HE:
            if kw.lower() in text_lower:
                score -= 1
                total += 1

        if total == 0:
            return 0.0
        return max(-1.0, min(1.0, score / total))

    async def analyze_sentiment(self, ticker: str) -> SentimentReport:
        """Scan news sources and compute sentiment for a ticker.

        Args:
            ticker: Stock ticker (e.g., 'TEVA', 'AAPL').

        Returns:
            SentimentReport with score, headlines, and summaries.
        """
        # Check cache first
        cached = await cache.get_news_sentiment(ticker)
        if cached:
            logger.debug("sentiment_cache_hit", ticker=ticker)
            return SentimentReport(**cached)

        logger.info("sentiment_analysis_start", ticker=ticker)

        # Build search queries
        queries = [
            f"{ticker} stock news today",
            f"{ticker} מניה חדשות",  # Hebrew
            f"site:globes.co.il {ticker}",
            f"site:bizportal.co.il {ticker}",
            f"site:cnbc.com {ticker}",
            f"site:reuters.com {ticker}",
        ]

        all_results: list[dict] = []
        for query in queries:
            results = await self._search_google_mcp(query, num_results=5)
            all_results.extend(results)
            await asyncio.sleep(0.5)  # Respect rate limits

        if not all_results:
            logger.warning("no_news_results", ticker=ticker)
            report = SentimentReport(
                ticker=ticker,
                timestamp=now_utc().isoformat(),
                score=0.0,
                headline_count=0,
                summary_en="No news found.",
            )
            return report

        # Deduplicate
        seen_titles: set[str] = set()
        unique_results = []
        for r in all_results:
            title = r.get("title", "")
            if title and title not in seen_titles:
                seen_titles.add(title)
                unique_results.append(r)

        # Classify Hebrew vs English headlines
        headlines_he = []
        headlines_en = []
        sources = set()
        scores = []

        for result in unique_results:
            title = result.get("title", "")
            url = result.get("url", "")
            snippet = result.get("snippet", "")
            combined_text = f"{title} {snippet}"

            # Hebrew detection: contains Hebrew characters
            is_hebrew = bool(re.search(r"[\u0590-\u05FF]", combined_text))
            if is_hebrew:
                headlines_he.append(title)
            else:
                headlines_en.append(title)

            scores.append(self._score_headline(combined_text))
            domain = url.split("/")[2] if url.count("/") >= 2 else url
            sources.add(domain)

        avg_score = sum(scores) / len(scores) if scores else 0.0

        # Build summary strings
        pos_count = sum(1 for s in scores if s > 0)
        neg_count = sum(1 for s in scores if s < 0)
        neu_count = len(scores) - pos_count - neg_count

        summary_en = (
            f"{ticker} sentiment: {pos_count} positive, {neg_count} negative, "
            f"{neu_count} neutral across {len(unique_results)} articles."
        )
        summary_he = (
            f"סנטימנט {ticker}: {pos_count} חיובי, {neg_count} שלילי, "
            f"{neu_count} ניטרלי מתוך {len(unique_results)} כתבות."
        )

        report = SentimentReport(
            ticker=ticker,
            timestamp=now_utc().isoformat(),
            score=round(avg_score, 4),
            headline_count=len(unique_results),
            sources=list(sources),
            headlines_he=headlines_he[:5],
            headlines_en=headlines_en[:5],
            summary_he=summary_he,
            summary_en=summary_en,
        )

        # Cache result
        await cache.cache_news_sentiment(
            ticker,
            {
                "ticker": report.ticker,
                "timestamp": report.timestamp,
                "score": report.score,
                "headline_count": report.headline_count,
                "sources": report.sources,
                "headlines_he": report.headlines_he,
                "headlines_en": report.headlines_en,
                "summary_he": report.summary_he,
                "summary_en": report.summary_en,
                "emoji": report.emoji,
            },
        )

        logger.info(
            "sentiment_analysis_complete",
            ticker=ticker,
            score=report.score,
            headline_count=report.headline_count,
        )
        return report

    async def health_check(self) -> dict[str, str]:
        """Check connectivity to Google Search MCP."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self._mcp_base_url}/health")
                if response.status_code == 200:
                    return {"status": "ok", "detail": "Google Search MCP connected"}
                return {
                    "status": "degraded",
                    "detail": f"MCP returned {response.status_code}",
                }
        except Exception:
            fallback_ok = bool(
                settings.google_api_key and settings.google_search_engine_id
            )
            if fallback_ok:
                return {
                    "status": "degraded",
                    "detail": "MCP offline, using Google API fallback",
                }
            return {
                "status": "error",
                "detail": "MCP offline and no API credentials configured",
            }
