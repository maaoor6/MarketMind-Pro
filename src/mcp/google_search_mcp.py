"""Google Search MCP Server — exposes web search as HTTP endpoints for AI agents."""

import json
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

mcp_app = Server("google-search-mcp")
http_app = FastAPI(title="Google Search MCP", version="1.0.0")

FINANCIAL_SITES = [
    "globes.co.il",
    "bizportal.co.il",
    "cnbc.com",
    "reuters.com",
    "bloomberg.com",
    "marketwatch.com",
    "themarker.com",
    "calcalist.co.il",
]


# ── HTTP endpoints (used by NewsSearchAgent) ──────────────────────────────────


@http_app.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    api_configured = bool(settings.google_api_key and settings.google_search_engine_id)
    return {
        "status": "ok",
        "detail": "Google Search MCP running",
        "api_configured": api_configured,
    }


@http_app.post("/tools/search_web")
async def search_web_endpoint(body: dict) -> JSONResponse:
    """Search the web for financial news."""
    results = await _search_web(
        query=body.get("query", ""),
        num_results=body.get("num_results", 5),
        site_filter=body.get("site_filter"),
    )
    data = json.loads(results[0].text)
    return JSONResponse(content=data)


@http_app.post("/tools/scrape_page")
async def scrape_page_endpoint(body: dict) -> JSONResponse:
    """Scrape a financial news article."""
    results = await _scrape_page(url=body.get("url", ""))
    data = json.loads(results[0].text)
    return JSONResponse(content=data)


@http_app.post("/tools/search_financial_news")
async def search_financial_news_endpoint(body: dict) -> JSONResponse:
    """Search for financial news about a ticker."""
    results = await _search_financial_news(
        ticker=body.get("ticker", ""),
        language=body.get("language", "both"),
    )
    data = json.loads(results[0].text)
    return JSONResponse(content=data)


# ── MCP tool declarations ─────────────────────────────────────────────────────


@mcp_app.list_tools()
async def list_tools() -> list[Tool]:
    """Declare available tools to MCP clients."""
    return [
        Tool(
            name="search_web",
            description=(
                "Search the web for financial news and market information. "
                "Returns titles, URLs, and snippets from top search results. "
                "Optimized for TASE and US market news sources."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (English or Hebrew supported)",
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "Number of results to return (1-10)",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 10,
                    },
                    "site_filter": {
                        "type": "string",
                        "description": "Restrict to a specific site (e.g., 'globes.co.il')",
                        "enum": FINANCIAL_SITES,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="scrape_page",
            description="Fetch the text content of a financial news article URL.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL of the article to scrape",
                    },
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="search_financial_news",
            description=(
                "Search specifically for financial news about a ticker symbol "
                "across Globes, Bizportal, CNBC, and Reuters simultaneously."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "Stock ticker symbol (e.g., TEVA, AAPL)",
                    },
                    "language": {
                        "type": "string",
                        "enum": ["en", "he", "both"],
                        "default": "both",
                        "description": "Language preference for results",
                    },
                },
                "required": ["ticker"],
            },
        ),
    ]


@mcp_app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Dispatch tool calls to implementations."""
    if name == "search_web":
        return await _search_web(
            query=arguments["query"],
            num_results=arguments.get("num_results", 5),
            site_filter=arguments.get("site_filter"),
        )
    elif name == "scrape_page":
        return await _scrape_page(url=arguments["url"])
    elif name == "search_financial_news":
        return await _search_financial_news(
            ticker=arguments["ticker"],
            language=arguments.get("language", "both"),
        )
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


# ── Tool implementations ──────────────────────────────────────────────────────


async def _search_web(
    query: str,
    num_results: int = 5,
    site_filter: str | None = None,
) -> list[TextContent]:
    """Execute Google Custom Search API call."""
    api_key = settings.google_api_key
    cx = settings.google_search_engine_id

    if not api_key or not cx:
        return [
            TextContent(
                type="text",
                text=json.dumps({"error": "Google API credentials not configured"}),
            )
        ]

    full_query = f"site:{site_filter} {query}" if site_filter else query

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            response = await client.get(
                "https://www.googleapis.com/customsearch/v1",
                params={
                    "key": api_key,
                    "cx": cx,
                    "q": full_query,
                    "num": min(num_results, 10),
                },
            )
            response.raise_for_status()
            data = response.json()
            items = data.get("items", [])
            results = [
                {
                    "title": item.get("title", ""),
                    "url": item.get("link", ""),
                    "snippet": item.get("snippet", ""),
                    "source": item.get("displayLink", ""),
                }
                for item in items
            ]
            return [
                TextContent(
                    type="text",
                    text=json.dumps({"results": results, "query": full_query}),
                )
            ]
        except Exception as exc:
            logger.error("google_search_mcp_failed", error=str(exc))
            return [TextContent(type="text", text=json.dumps({"error": str(exc)}))]


async def _scrape_page(url: str) -> list[TextContent]:
    """Fetch page content from a URL."""
    allowed_domains = set(FINANCIAL_SITES) | {"sec.gov", "tase.co.il"}
    domain = url.split("/")[2] if url.count("/") >= 2 else ""
    if not any(allowed in domain for allowed in allowed_domains):
        return [
            TextContent(
                type="text",
                text=json.dumps({"error": f"Domain {domain} not in allowed list"}),
            )
        ]

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        try:
            headers = {"User-Agent": "MarketMind-Pro/1.0 (Research Bot)"}
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            text = response.text[:5000]
            return [
                TextContent(type="text", text=json.dumps({"url": url, "content": text}))
            ]
        except Exception as exc:
            return [TextContent(type="text", text=json.dumps({"error": str(exc)}))]


async def _search_financial_news(
    ticker: str, language: str = "both"
) -> list[TextContent]:
    """Multi-source ticker news search."""
    queries = []
    if language in ("en", "both"):
        queries += [f"{ticker} stock news", f"{ticker} earnings analysis"]
    if language in ("he", "both"):
        queries += [f"{ticker} מניה", f"אנליזה {ticker}"]

    all_results = []
    for query in queries:
        results_content = await _search_web(query, num_results=3)
        for content in results_content:
            try:
                data = json.loads(content.text)
                all_results.extend(data.get("results", []))
            except json.JSONDecodeError:
                pass

    seen = set()
    unique = []
    for r in all_results:
        url = r.get("url", "")
        if url and url not in seen:
            seen.add(url)
            unique.append(r)

    return [
        TextContent(
            type="text", text=json.dumps({"ticker": ticker, "results": unique[:15]})
        )
    ]


async def main() -> None:
    """Run as HTTP server (for Docker) or stdio (for MCP clients like Claude Desktop)."""
    import sys

    if "--stdio" in sys.argv:
        logger.info("google_search_mcp_starting", mode="stdio")
        async with stdio_server() as (read_stream, write_stream):
            await mcp_app.run(
                read_stream, write_stream, mcp_app.create_initialization_options()
            )
    else:
        port = settings.google_search_mcp_port
        logger.info("google_search_mcp_starting", mode="http", port=port)
        config = uvicorn.Config(
            http_app, host="0.0.0.0", port=port, log_level="warning"  # nosec B104
        )
        server = uvicorn.Server(config)
        await server.serve()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
