"""News Search Agent — multi-source news sentiment with Google News RSS fallback."""

import asyncio
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from xml.etree.ElementTree import ParseError as XMLParseError

import httpx
from defusedxml.ElementTree import fromstring as safe_fromstring

from src.database.cache import cache
from src.utils.config import settings
from src.utils.logger import get_logger
from src.utils.timezone_utils import now_utc

logger = get_logger(__name__)

# Professional browser headers to avoid 403/429 from financial news sites
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Google News RSS — returns results from Bloomberg, Reuters, CNBC, etc.
# Free, no API key required, reliably multi-source.
_GOOGLE_NEWS_RSS = (
    "https://news.google.com/rss/search" "?q={ticker}+stock+news&hl=en&gl=US&ceid=US:en"
)

# Yahoo Finance headline RSS (ticker-specific, official)
_YAHOO_FINANCE_RSS = (
    "https://feeds.finance.yahoo.com/rss/2.0/headline"
    "?s={ticker}&region=US&lang=en-US"
)

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


def _time_ago(pub_date_str: str) -> str:
    """Convert an RFC 2822 pubDate string to a human-readable 'X hours ago' string."""
    try:
        pub_dt = parsedate_to_datetime(pub_date_str)
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=UTC)
        now = datetime.now(tz=UTC)
        delta = now - pub_dt
        total_seconds = int(delta.total_seconds())
        if total_seconds < 60:
            return "just now"
        if total_seconds < 3600:
            m = total_seconds // 60
            return f"{m}m ago"
        if total_seconds < 86400:
            h = total_seconds // 3600
            return f"{h}h ago"
        d = total_seconds // 86400
        return f"{d}d ago"
    except Exception:
        return ""


def _strip_html(text: str) -> str:
    """Remove HTML tags from a string."""
    return re.sub(r"<[^>]+>", "", text).strip()


def _parse_rss(xml_text: str, fallback_source: str, max_items: int = 8) -> list[dict]:
    """Parse an RSS XML string and return normalised article dicts.

    Handles both standard RSS and Google News RSS format (which puts the real
    source name in a <source> child element of each <item>).
    """
    try:
        root = safe_fromstring(xml_text)
    except XMLParseError:
        return []

    results = []
    for item in root.findall(".//item")[:max_items]:
        title_raw = item.findtext("title") or ""
        link = item.findtext("link") or ""
        description = _strip_html(item.findtext("description") or "")[:200]
        pub_date = item.findtext("pubDate") or ""

        # Google News puts the real publisher in <source>; strip it from title
        source_el = item.find("source")
        if source_el is not None and source_el.text:
            source_name = source_el.text.strip()
            # Google News appends " - Source" to the title; remove it
            title = re.sub(
                rf"\s*[-–]\s*{re.escape(source_name)}\s*$", "", title_raw
            ).strip()
        else:
            source_name = fallback_source
            title = title_raw.strip()

        if not title:
            continue

        results.append(
            {
                "title": title,
                "url": link.strip(),
                "snippet": description,
                "source": source_name,
                "published_at": pub_date,
                "time_ago": _time_ago(pub_date) if pub_date else "",
            }
        )
    return results


@dataclass
class SentimentReport:
    ticker: str
    timestamp: str
    score: float  # -1.0 (very negative) to +1.0 (very positive)
    headline_count: int
    sources: list[str] = field(default_factory=list)
    headlines: list[str] = field(default_factory=list)
    summary: str = ""
    recent_headlines: list[dict] = field(
        default_factory=list
    )  # {title, snippet, url, source, time_ago}
    emoji: str = "⚪"

    def __post_init__(self) -> None:
        if self.score >= 0.3:
            self.emoji = "🟢"
        elif self.score <= -0.3:
            self.emoji = "🔴"
        else:
            self.emoji = "🟡"


class NewsSearchAgent:
    """Autonomous agent for news sentiment analysis.

    Priority:
    1. Google Search MCP (if running on port 8001)
    2. Google Custom Search API (if GOOGLE_API_KEY configured)
    3. Google News RSS + Yahoo Finance RSS (free, no API key needed)
    """

    def __init__(self) -> None:
        self._mcp_base_url = f"http://localhost:{settings.google_search_mcp_port}"

    # ── Primary: Google Search MCP ───────────────────────────────────────────

    async def _search_google_mcp(self, query: str, num_results: int = 10) -> list[dict]:
        """Call Google Search MCP server. Returns [] on any failure."""
        try:
            async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as client:
                response = await client.post(
                    f"{self._mcp_base_url}/tools/search_web",
                    json={"query": query, "num_results": num_results},
                )
                response.raise_for_status()
                return response.json().get("results", [])
        except httpx.ConnectError:
            logger.warning("mcp_unavailable", query=query)
        except Exception as exc:
            logger.warning("mcp_error", query=query, error=str(exc))
        return []

    async def _google_custom_search_fallback(
        self, query: str, num_results: int = 10
    ) -> list[dict]:
        """Direct Google Custom Search API call. Returns [] if not configured."""
        api_key = settings.google_api_key
        cx = settings.google_search_engine_id
        if not api_key or not cx:
            return []

        url = "https://www.googleapis.com/customsearch/v1"
        params = {"key": api_key, "cx": cx, "q": query, "num": min(num_results, 10)}
        async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as client:
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
                items = response.json().get("items", [])
                return [
                    {
                        "title": item.get("title", ""),
                        "url": item.get("link", ""),
                        "snippet": item.get("snippet", ""),
                        "source": item.get("displayLink", ""),
                        "time_ago": "",
                    }
                    for item in items
                ]
            except Exception as exc:
                logger.error("google_search_failed", error=str(exc))
                return []

    # ── RSS fetching ──────────────────────────────────────────────────────────

    async def _fetch_rss(
        self, url: str, fallback_source: str, max_items: int = 8
    ) -> list[dict]:
        """Fetch and parse one RSS feed. Returns [] on any error."""
        try:
            async with httpx.AsyncClient(
                timeout=12, headers=_HEADERS, follow_redirects=True
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                results = _parse_rss(resp.text, fallback_source, max_items)
                logger.debug("rss_fetched", source=fallback_source, count=len(results))
                return results
        except Exception as exc:
            logger.debug("rss_fetch_failed", source=fallback_source, error=str(exc))
            return []

    async def _fetch_rss_for_ticker(self, ticker: str) -> list[dict]:
        """Fetch Google News RSS + Yahoo Finance RSS in parallel for a ticker.

        Google News RSS aggregates results from Bloomberg, Reuters, CNBC, WSJ, etc.
        Each article includes the real publisher name from the <source> element.
        Results are deduplicated by title; max 2 per source domain for diversity.
        """
        ticker_upper = ticker.upper()
        google_url = _GOOGLE_NEWS_RSS.format(ticker=ticker_upper)
        yahoo_url = _YAHOO_FINANCE_RSS.format(ticker=ticker_upper)

        google_results, yahoo_results = await asyncio.gather(
            self._fetch_rss(google_url, "Google News", max_items=10),
            self._fetch_rss(yahoo_url, "Yahoo Finance", max_items=5),
        )

        all_items = google_results + yahoo_results
        seen_titles: set[str] = set()
        source_counts: dict[str, int] = {}
        merged: list[dict] = []

        for item in all_items:
            title = item.get("title", "")
            if not title or title in seen_titles:
                continue
            source = item.get("source", "")
            if source_counts.get(source, 0) >= 2:
                continue
            seen_titles.add(title)
            source_counts[source] = source_counts.get(source, 0) + 1
            merged.append(item)

        logger.info(
            "rss_fetch_complete",
            ticker=ticker,
            count=len(merged),
            sources=list(source_counts.keys()),
        )
        return merged

    # ── Sentiment scoring ─────────────────────────────────────────────────────

    def _score_headline(self, text: str) -> float:
        """Score a headline from -1.0 to +1.0 based on keyword matching."""
        text_lower = text.lower()
        score = 0.0
        total = 0
        for kw in POSITIVE_EN:
            if kw.lower() in text_lower:
                score += 1
                total += 1
        for kw in NEGATIVE_EN:
            if kw.lower() in text_lower:
                score -= 1
                total += 1
        if total == 0:
            return 0.0
        return max(-1.0, min(1.0, score / total))

    # ── Main analysis ─────────────────────────────────────────────────────────

    async def analyze_sentiment(self, ticker: str) -> SentimentReport:
        """Scan news sources and compute sentiment for a ticker."""
        cached = await cache.get_news_sentiment(ticker)
        if cached:
            logger.debug("sentiment_cache_hit", ticker=ticker)
            return SentimentReport(**cached)

        logger.info("sentiment_analysis_start", ticker=ticker)

        all_results: list[dict] = []

        # Try Google MCP first
        mcp_results = await self._search_google_mcp(
            f"{ticker} stock news today", num_results=10
        )
        if not mcp_results:
            mcp_results = await self._google_custom_search_fallback(
                f"{ticker} stock news", num_results=10
            )

        if mcp_results:
            all_results.extend(mcp_results)
        else:
            logger.info("using_rss_fallback", ticker=ticker)
            all_results = await self._fetch_rss_for_ticker(ticker)

        if not all_results:
            logger.warning("no_news_results", ticker=ticker)
            return SentimentReport(
                ticker=ticker,
                timestamp=now_utc().isoformat(),
                score=0.0,
                headline_count=0,
                summary="No news found.",
            )

        # Deduplicate
        seen: set[str] = set()
        unique_results: list[dict] = []
        for r in all_results:
            t = r.get("title", "")
            if t and t not in seen:
                seen.add(t)
                unique_results.append(r)

        headlines = []
        sources: set[str] = set()
        scores = []

        for result in unique_results:
            title = result.get("title", "")
            snippet = result.get("snippet", "")
            url = result.get("url", "")
            headlines.append(title)
            scores.append(self._score_headline(f"{title} {snippet}"))
            domain = url.split("/")[2] if url.count("/") >= 2 else url
            sources.add(domain)

        avg_score = sum(scores) / len(scores) if scores else 0.0
        pos_count = sum(1 for s in scores if s > 0)
        neg_count = sum(1 for s in scores if s < 0)
        neu_count = len(scores) - pos_count - neg_count

        summary = (
            f"{ticker} sentiment: {pos_count} positive, {neg_count} negative, "
            f"{neu_count} neutral across {len(unique_results)} articles."
        )

        # Sort by publication date (newest first) before picking top 5
        def _pub_ts(r: dict) -> float:
            try:
                from email.utils import parsedate_to_datetime

                return parsedate_to_datetime(r["published_at"]).timestamp()
            except Exception:
                return 0.0

        unique_results.sort(key=_pub_ts, reverse=True)

        # Filter out articles older than 48 hours
        now_ts = datetime.now(tz=UTC).timestamp()
        fresh_results = [
            r
            for r in unique_results
            if now_ts - _pub_ts(r) <= 172800  # 48 hours in seconds
        ] or unique_results  # fall back to all if nothing is fresh

        # Top 5 with source diversity (max 1 per source name in top 5)
        top5: list[dict] = []
        seen_sources: set[str] = set()
        for r in fresh_results:
            src = r.get("source", "")
            if src in seen_sources:
                continue
            seen_sources.add(src)
            top5.append(
                {
                    "title": r.get("title", ""),
                    "snippet": r.get("snippet", "")[:150],
                    "url": r.get("url", ""),
                    "source": src,
                    "time_ago": r.get("time_ago", ""),
                }
            )
            if len(top5) >= 5:
                break

        report = SentimentReport(
            ticker=ticker,
            timestamp=now_utc().isoformat(),
            score=round(avg_score, 4),
            headline_count=len(unique_results),
            sources=list(sources),
            headlines=headlines[:5],
            summary=summary,
            recent_headlines=top5,
        )

        await cache.cache_news_sentiment(
            ticker,
            {
                "ticker": report.ticker,
                "timestamp": report.timestamp,
                "score": report.score,
                "headline_count": report.headline_count,
                "sources": report.sources,
                "headlines": report.headlines,
                "summary": report.summary,
                "recent_headlines": report.recent_headlines,
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
                "status": "degraded",
                "detail": "MCP offline — using Google News RSS fallback",
            }
