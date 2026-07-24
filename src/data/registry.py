"""Provider registry + composite fail-over router.

``get_provider()`` returns a :class:`CompositeProvider` built from the
``DATA_PROVIDER_PRIORITY`` setting (comma-separated provider names, tried in
order). Each capability call walks the priority list and returns the first
provider that answers; a :class:`~src.data.provider.NotSupportedError` or any
fetch error fails over to the next. Results carry provenance
(:class:`~src.data.provider.ProviderResult`) for Phase-1 cross-validation.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

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
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

# name → zero-arg factory. Phase 1 registers edgar/stooq/finnhub/alphavantage.
_FACTORIES: dict[str, Callable[[], MarketDataProvider]] = {}


def register_provider(name: str, factory: Callable[[], MarketDataProvider]) -> None:
    """Register a provider factory under ``name`` (lower-cased)."""
    _FACTORIES[name.lower()] = factory


def _register_defaults() -> None:
    """Register the built-in providers (import-time, lazy factories)."""
    if "yfinance" in _FACTORIES:
        return
    from src.data.providers.yfinance_provider import YFinanceProvider

    register_provider("yfinance", YFinanceProvider)


class CompositeProvider(MarketDataProvider):
    """Routes each capability to the first provider in priority order that answers."""

    name = "composite"

    def __init__(self, providers: Iterable[MarketDataProvider]) -> None:
        self._providers: list[MarketDataProvider] = list(providers)
        caps: set[str] = set()
        for p in self._providers:
            caps |= set(p.capabilities)
        self.capabilities = frozenset(caps)

    @property
    def providers(self) -> list[MarketDataProvider]:
        """The ordered underlying providers."""
        return list(self._providers)

    def _candidates(self, capability: str) -> list[MarketDataProvider]:
        return [p for p in self._providers if p.supports(capability)]

    async def _try(
        self, capability: str, method: str, *args: object, **kwargs: object
    ) -> ProviderResult:
        candidates = self._candidates(capability)
        if not candidates:
            raise NotSupportedError(f"no provider supports {capability}")
        last_exc: Exception | None = None
        for provider in candidates:
            try:
                data = await getattr(provider, method)(*args, **kwargs)
                return ProviderResult(data=data, source=provider.name)
            except NotSupportedError:
                continue
            except Exception as exc:  # noqa: BLE001 — fail over to the next source
                last_exc = exc
                logger.warning(
                    "provider_failover",
                    provider=provider.name,
                    capability=capability,
                    error=type(exc).__name__,
                )
                continue
        if last_exc is not None:
            raise last_exc
        raise NotSupportedError(f"no provider served {capability}")

    # ── Provenance-carrying variants (used by Phase-1 validation) ──────────
    async def fetch_ohlcv_result(
        self, ticker: str, period: str = "1y", interval: str = "1d"
    ) -> ProviderResult:
        return await self._try(CAP_OHLCV, "fetch_ohlcv", ticker, period, interval)

    async def fetch_live_price_result(self, ticker: str) -> ProviderResult:
        return await self._try(CAP_LIVE_PRICE, "fetch_live_price", ticker)

    # ── Plain variants (byte-identical returns to the old inline calls) ────
    async def fetch_ohlcv(self, ticker, period="1y", interval="1d"):
        return (await self.fetch_ohlcv_result(ticker, period, interval)).data

    async def fetch_live_price(self, ticker):
        return (await self.fetch_live_price_result(ticker)).data

    async def fetch_fundamentals(self, ticker):
        return (await self._try(CAP_FUNDAMENTALS, "fetch_fundamentals", ticker)).data

    async def fetch_insiders(self, ticker):
        return (await self._try(CAP_INSIDERS, "fetch_insiders", ticker)).data

    async def screen(self, screen_name, count=25):
        return (await self._try(CAP_SCREEN, "screen", screen_name, count=count)).data

    async def health_check(self) -> dict[str, str]:
        details = []
        for provider in self._providers:
            res = await provider.health_check()
            details.append(f"{provider.name}:{res.get('status')}")
        ok = any(d.endswith(":ok") for d in details)
        return {
            "status": "ok" if ok else "error",
            "detail": ", ".join(details) or "no providers",
        }


_CACHED: CompositeProvider | None = None


def _build_from_priority(priority: str) -> CompositeProvider:
    _register_defaults()
    names = [n.strip().lower() for n in priority.split(",") if n.strip()]
    if not names:
        names = ["yfinance"]
    providers: list[MarketDataProvider] = []
    for name in names:
        factory = _FACTORIES.get(name)
        if factory is None:
            logger.warning("unknown_data_provider", name=name)
            continue
        try:
            providers.append(factory())
        except (
            Exception
        ) as exc:  # noqa: BLE001 — a broken provider must not brick the app
            logger.warning(
                "data_provider_init_failed", name=name, error=type(exc).__name__
            )
    if not providers:  # last-resort fail-safe: always have yfinance
        from src.data.providers.yfinance_provider import YFinanceProvider

        providers.append(YFinanceProvider())
    return CompositeProvider(providers)


def get_provider() -> CompositeProvider:
    """Return the process-wide composite provider (cached)."""
    global _CACHED
    if _CACHED is None:
        _CACHED = _build_from_priority(
            getattr(settings, "data_provider_priority", "yfinance")
        )
    return _CACHED


def reset_provider_cache() -> None:
    """Drop the cached provider (tests / config reload)."""
    global _CACHED
    _CACHED = None
