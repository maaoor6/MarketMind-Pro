"""SQL MCP Server — AI-accessible structured data queries over PostgreSQL."""

import json
from datetime import datetime
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool
from sqlalchemy import select, func, text

from src.database.models import PriceHistory, DualListingGap, UserAlert, SentimentRecord
from src.database.session import AsyncSessionLocal
from src.utils.logger import get_logger

logger = get_logger(__name__)

app = Server("sql-mcp-server")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="query_prices",
            description="Query historical OHLCV price data for a ticker from the database.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Ticker symbol"},
                    "from_date": {"type": "string", "description": "Start date ISO format (YYYY-MM-DD)"},
                    "to_date": {"type": "string", "description": "End date ISO format (YYYY-MM-DD)"},
                    "limit": {"type": "integer", "default": 100, "maximum": 1000},
                },
                "required": ["ticker"],
            },
        ),
        Tool(
            name="get_arbitrage_history",
            description="Retrieve historical arbitrage gaps for dual-listed stocks.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker_us": {"type": "string"},
                    "min_gap_pct": {"type": "number", "description": "Minimum gap percentage filter"},
                    "limit": {"type": "integer", "default": 50},
                },
                "required": ["ticker_us"],
            },
        ),
        Tool(
            name="get_alerts",
            description="Retrieve active user alerts.",
            inputSchema={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "ticker": {"type": "string"},
                },
            },
        ),
        Tool(
            name="get_sentiment_history",
            description="Get historical sentiment scores for a ticker.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "limit": {"type": "integer", "default": 30},
                },
                "required": ["ticker"],
            },
        ),
        Tool(
            name="get_volume_spikes",
            description="Query days where volume exceeded the 10-day MA by 2x or more.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "limit": {"type": "integer", "default": 20},
                },
                "required": ["ticker"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Dispatch MCP tool calls."""
    try:
        if name == "query_prices":
            return await _query_prices(**arguments)
        elif name == "get_arbitrage_history":
            return await _get_arbitrage_history(**arguments)
        elif name == "get_alerts":
            return await _get_alerts(**arguments)
        elif name == "get_sentiment_history":
            return await _get_sentiment_history(**arguments)
        elif name == "get_volume_spikes":
            return await _get_volume_spikes(**arguments)
        else:
            return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]
    except Exception as exc:
        logger.error("sql_mcp_tool_failed", tool=name, error=str(exc))
        return [TextContent(type="text", text=json.dumps({"error": str(exc)}))]


async def _query_prices(
    ticker: str,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 100,
) -> list[TextContent]:
    async with AsyncSessionLocal() as session:
        stmt = (
            select(PriceHistory)
            .where(PriceHistory.ticker == ticker.upper())
            .order_by(PriceHistory.timestamp.desc())
            .limit(limit)
        )
        if from_date:
            stmt = stmt.where(PriceHistory.timestamp >= from_date)
        if to_date:
            stmt = stmt.where(PriceHistory.timestamp <= to_date)

        result = await session.execute(stmt)
        rows = result.scalars().all()
        data = [
            {
                "timestamp": str(r.timestamp),
                "open": float(r.open),
                "high": float(r.high),
                "low": float(r.low),
                "close": float(r.close),
                "volume": r.volume,
            }
            for r in rows
        ]
    return [TextContent(type="text", text=json.dumps({"ticker": ticker, "count": len(data), "data": data}))]


async def _get_arbitrage_history(
    ticker_us: str,
    min_gap_pct: float | None = None,
    limit: int = 50,
) -> list[TextContent]:
    async with AsyncSessionLocal() as session:
        stmt = (
            select(DualListingGap)
            .where(DualListingGap.ticker_us == ticker_us.upper())
            .order_by(DualListingGap.timestamp.desc())
            .limit(limit)
        )
        if min_gap_pct is not None:
            stmt = stmt.where(DualListingGap.gap_pct >= min_gap_pct)

        result = await session.execute(stmt)
        rows = result.scalars().all()
        data = [
            {
                "timestamp": str(r.timestamp),
                "gap_pct": float(r.gap_pct),
                "gap_direction": r.gap_direction,
                "price_us_usd": float(r.price_us_usd),
                "price_tase_in_usd": float(r.price_tase_in_usd),
            }
            for r in rows
        ]
    return [TextContent(type="text", text=json.dumps({"ticker_us": ticker_us, "count": len(data), "data": data}))]


async def _get_alerts(
    chat_id: str | None = None,
    ticker: str | None = None,
) -> list[TextContent]:
    async with AsyncSessionLocal() as session:
        stmt = select(UserAlert).where(UserAlert.is_active == True)  # noqa: E712
        if chat_id:
            stmt = stmt.where(UserAlert.chat_id == chat_id)
        if ticker:
            stmt = stmt.where(UserAlert.ticker == ticker.upper())

        result = await session.execute(stmt)
        rows = result.scalars().all()
        data = [
            {
                "id": r.id,
                "ticker": r.ticker,
                "alert_type": r.alert_type,
                "threshold": float(r.threshold) if r.threshold else None,
                "chat_id": r.chat_id,
            }
            for r in rows
        ]
    return [TextContent(type="text", text=json.dumps({"count": len(data), "alerts": data}))]


async def _get_sentiment_history(ticker: str, limit: int = 30) -> list[TextContent]:
    async with AsyncSessionLocal() as session:
        stmt = (
            select(SentimentRecord)
            .where(SentimentRecord.ticker == ticker.upper())
            .order_by(SentimentRecord.timestamp.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()
        data = [
            {"timestamp": str(r.timestamp), "score": float(r.score), "headline_count": r.headline_count}
            for r in rows
        ]
    return [TextContent(type="text", text=json.dumps({"ticker": ticker, "sentiment_history": data}))]


async def _get_volume_spikes(ticker: str, limit: int = 20) -> list[TextContent]:
    """Return days where raw volume is in the top 10% for this ticker."""
    async with AsyncSessionLocal() as session:
        subq = (
            select(func.percentile_cont(0.9).within_group(PriceHistory.volume))
            .where(PriceHistory.ticker == ticker.upper())
            .scalar_subquery()
        )
        stmt = (
            select(PriceHistory)
            .where(PriceHistory.ticker == ticker.upper())
            .where(PriceHistory.volume >= subq)
            .order_by(PriceHistory.timestamp.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()
        data = [
            {"timestamp": str(r.timestamp), "volume": r.volume, "close": float(r.close)}
            for r in rows
        ]
    return [TextContent(type="text", text=json.dumps({"ticker": ticker, "volume_spikes": data}))]


async def main() -> None:
    logger.info("sql_mcp_server_starting")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
