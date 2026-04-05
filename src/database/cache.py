"""Redis cache layer for high-frequency quote caching."""

import json
from typing import Any

import redis.asyncio as aioredis

from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


class RedisCache:
    """Async Redis cache wrapper with JSON serialization."""

    def __init__(self) -> None:
        self._client: aioredis.Redis | None = None

    async def connect(self) -> None:
        """Initialize Redis connection pool."""
        self._client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        await self._client.ping()
        logger.info("redis_connected", url=settings.redis_url)

    async def disconnect(self) -> None:
        """Close Redis connection."""
        if self._client:
            await self._client.aclose()

    async def get(self, key: str) -> Any | None:
        """Get value by key. Returns deserialized Python object or None."""
        if not self._client:
            return None
        value = await self._client.get(key)
        if value is None:
            return None
        return json.loads(value)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Set key with optional TTL in seconds."""
        if not self._client:
            return
        serialized = json.dumps(value, default=str)
        if ttl:
            await self._client.setex(key, ttl, serialized)
        else:
            await self._client.set(key, serialized)

    async def delete(self, key: str) -> None:
        """Delete a key."""
        if self._client:
            await self._client.delete(key)

    async def health_check(self) -> dict[str, str]:
        """Return Redis health status."""
        try:
            if not self._client:
                await self.connect()
            await self._client.ping()
            return {"status": "ok", "detail": "Redis connected"}
        except Exception as exc:
            return {"status": "error", "detail": str(exc)}

    # ── Quote-specific helpers ────────────────────────────────────────

    def quote_key(self, ticker: str, timeframe: str = "1m") -> str:
        return f"quote:{ticker}:{timeframe}"

    async def cache_quote(self, ticker: str, data: dict[str, Any]) -> None:
        """Cache a 1-minute quote with default TTL."""
        key = self.quote_key(ticker)
        await self.set(key, data, ttl=settings.quote_cache_ttl)
        logger.debug("quote_cached", ticker=ticker, ttl=settings.quote_cache_ttl)

    async def get_quote(self, ticker: str) -> dict[str, Any] | None:
        """Retrieve cached quote. Returns None if expired or missing."""
        return await self.get(self.quote_key(ticker))

    async def cache_news_sentiment(self, ticker: str, data: dict[str, Any]) -> None:
        """Cache news sentiment with 15-minute TTL."""
        key = f"sentiment:{ticker}"
        await self.set(key, data, ttl=settings.news_cache_ttl)

    async def get_news_sentiment(self, ticker: str) -> dict[str, Any] | None:
        return await self.get(f"sentiment:{ticker}")


# Module-level singleton
cache = RedisCache()
