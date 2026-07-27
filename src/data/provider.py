"""The market-data provider interface + provenance wrapper.

A provider implements only the capabilities it supports and declares them in
:attr:`MarketDataProvider.capabilities`. Any capability not implemented raises
:class:`NotSupportedError`, which the :class:`~src.data.registry.CompositeProvider`
uses to skip to the next provider during fail-over. All methods are async and
must be pure I/O — caching lives one layer above (e.g. in ``QuantEngine``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from src.utils.timezone_utils import now_utc

# Capability identifiers — a provider declares which of these it can serve.
CAP_OHLCV = "ohlcv"
CAP_LIVE_PRICE = "live_price"
CAP_FUNDAMENTALS = "fundamentals"
CAP_INSIDERS = "insiders"
CAP_SCREEN = "screen"


class NotSupportedError(RuntimeError):
    """A provider does not implement the requested capability.

    Raised by the default :class:`MarketDataProvider` method bodies so that
    subclasses only override what they support, and so the composite router can
    fail over to the next provider.
    """


@dataclass(frozen=True)
class ProviderResult[T]:
    """A fetched value stamped with its origin, for provenance + validation.

    Attributes:
        data: The fetched payload (DataFrame, tuple, dict, …).
        source: The ``name`` of the provider that produced it.
        fetched_at: UTC timestamp of the fetch.
    """

    data: T
    source: str
    fetched_at: datetime = field(default_factory=now_utc)


class MarketDataProvider:
    """Base class for all market-data providers.

    Subclasses set :attr:`name` and :attr:`capabilities`, then override only
    the methods for the capabilities they declare. Unimplemented methods raise
    :class:`NotSupportedError`.
    """

    name: str = "base"
    capabilities: frozenset[str] = frozenset()

    def supports(self, capability: str) -> bool:
        """Return True if this provider declares ``capability``."""
        return capability in self.capabilities

    async def fetch_ohlcv(
        self,
        ticker: str,
        period: str = "1y",
        interval: str = "1d",
    ) -> pd.DataFrame:
        """Return an OHLCV DataFrame (columns: Open/High/Low/Close/Volume)."""
        raise NotSupportedError(f"{self.name} does not support {CAP_OHLCV}")

    async def fetch_live_price(self, ticker: str) -> tuple[float, float | None]:
        """Return ``(last_price, previous_close)``; prev_close may be None."""
        raise NotSupportedError(f"{self.name} does not support {CAP_LIVE_PRICE}")

    async def fetch_fundamentals(self, ticker: str) -> dict:
        """Return a raw fundamentals dict (vendor-shaped)."""
        raise NotSupportedError(f"{self.name} does not support {CAP_FUNDAMENTALS}")

    async def fetch_insiders(self, ticker: str) -> list[dict]:
        """Return a list of recent insider-transaction dicts."""
        raise NotSupportedError(f"{self.name} does not support {CAP_INSIDERS}")

    async def screen(self, screen_name: str, count: int = 25) -> dict:
        """Return a raw screener payload (vendor-shaped)."""
        raise NotSupportedError(f"{self.name} does not support {CAP_SCREEN}")

    async def health_check(self) -> dict[str, str]:
        """Lightweight reachability probe. Override for a real check."""
        return {"status": "ok", "detail": f"{self.name} provider (no probe)"}
