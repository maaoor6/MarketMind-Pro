"""Market-data provider abstraction layer.

Decouples the application from any single market-data vendor. Every price /
quote / fundamentals read funnels through a :class:`MarketDataProvider`, so
new free/official sources (Stooq, SEC EDGAR, Finnhub, …) can be added behind
the same interface and used for fail-over and cross-validation without
touching call sites.
"""

from src.data.provider import (
    CAP_FUNDAMENTALS,
    CAP_INSIDERS,
    CAP_LIVE_PRICE,
    CAP_OHLCV,
    CAP_SCREEN,
    MarketDataProvider,
    NotSupportedError,
    ProviderResult,
)
from src.data.registry import CompositeProvider, get_provider, reset_provider_cache

__all__ = [
    "MarketDataProvider",
    "NotSupportedError",
    "ProviderResult",
    "CompositeProvider",
    "get_provider",
    "reset_provider_cache",
    "CAP_OHLCV",
    "CAP_LIVE_PRICE",
    "CAP_FUNDAMENTALS",
    "CAP_INSIDERS",
    "CAP_SCREEN",
]
