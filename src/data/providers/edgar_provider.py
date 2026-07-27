"""SEC EDGAR provider — official U.S. company fundamentals, free, no key.

Pulls XBRL "company facts" from ``data.sec.gov`` — the authoritative source for
revenue, net income, diluted EPS and shares outstanding — for use as the
highest-trust **cross-check** of yfinance fundamentals and as a fail-over
fundamentals source. SEC's fair-access rules require a descriptive
``User-Agent`` with a contact (``settings.sec_edgar_user_agent``); no API key.

Only ``fetch_fundamentals`` is implemented here. Form-4 insider parsing and
earnings-calendar are intentionally left to a follow-up (the existing yfinance
insider path works and EDGAR's Form-4 stream needs its own normalization).
"""

from __future__ import annotations

import asyncio

import httpx

from src.data.provider import CAP_FUNDAMENTALS, MarketDataProvider, NotSupportedError
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# XBRL concept → our normalized field. First concept that resolves wins.
_FACT_CONCEPTS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
    ),
    "net_income": ("NetIncomeLoss",),
    "eps_diluted": ("EarningsPerShareDiluted",),
    "assets": ("Assets",),
    "stockholders_equity": ("StockholdersEquity",),
}
_SHARES_CONCEPT = "EntityCommonStockSharesOutstanding"  # under "dei"


class EdgarProvider(MarketDataProvider):
    """Official SEC fundamentals via the XBRL companyfacts API (no key)."""

    name = "edgar"
    capabilities = frozenset({CAP_FUNDAMENTALS})

    # Process-wide ticker→CIK cache (the map is ~10k rows, fetched once).
    _cik_map: dict[str, int] | None = None
    _cik_lock = asyncio.Lock()

    def __init__(self, timeout: float = 15.0) -> None:
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": settings.sec_edgar_user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Accept": "application/json",
        }

    async def _load_cik_map(self) -> dict[str, int]:
        if EdgarProvider._cik_map is not None:
            return EdgarProvider._cik_map
        async with EdgarProvider._cik_lock:
            if EdgarProvider._cik_map is not None:
                return EdgarProvider._cik_map
            async with httpx.AsyncClient(
                timeout=self._timeout, headers=self._headers()
            ) as client:
                resp = await client.get(_TICKERS_URL)
                resp.raise_for_status()
                raw = resp.json()
            mapping: dict[str, int] = {}
            for row in raw.values():
                sym = str(row.get("ticker", "")).upper()
                cik = row.get("cik_str")
                if sym and cik is not None:
                    mapping[sym] = int(cik)
            EdgarProvider._cik_map = mapping
            logger.info("edgar_cik_map_loaded", count=len(mapping))
            return mapping

    async def _cik_for(self, ticker: str) -> int:
        mapping = await self._load_cik_map()
        cik = mapping.get(ticker.strip().upper())
        if cik is None:
            raise NotSupportedError(f"edgar: no CIK for {ticker!r}")
        return cik

    @staticmethod
    def _latest_fact(facts: dict, concepts: tuple[str, ...]) -> float | None:
        """Return the most recent value across candidate XBRL concepts."""
        gaap = facts.get("us-gaap", {})
        best_val: float | None = None
        best_end = ""
        for concept in concepts:
            node = gaap.get(concept)
            if not node:
                continue
            for unit_rows in node.get("units", {}).values():
                for row in unit_rows:
                    end = str(row.get("end", ""))
                    val = row.get("val")
                    if val is None:
                        continue
                    if end > best_end:
                        best_end = end
                        best_val = float(val)
            if best_val is not None:
                break
        return best_val

    @staticmethod
    def _shares_outstanding(facts: dict) -> float | None:
        node = facts.get("dei", {}).get(_SHARES_CONCEPT)
        if not node:
            return None
        best_val: float | None = None
        best_end = ""
        for unit_rows in node.get("units", {}).values():
            for row in unit_rows:
                end = str(row.get("end", ""))
                val = row.get("val")
                if val is not None and end > best_end:
                    best_end = end
                    best_val = float(val)
        return best_val

    def _parse_facts(self, payload: dict, ticker: str) -> dict:
        """Reduce a companyfacts payload to our normalized fundamentals dict."""
        facts = payload.get("facts", {})
        out: dict = {
            "ticker": ticker.upper(),
            "source": "edgar",
            "entity_name": payload.get("entityName"),
            "cik": payload.get("cik"),
            "shares_outstanding": self._shares_outstanding(facts),
        }
        for field_name, concepts in _FACT_CONCEPTS.items():
            out[field_name] = self._latest_fact(facts, concepts)
        return out

    async def fetch_fundamentals(self, ticker: str) -> dict:
        cik = await self._cik_for(ticker)
        async with httpx.AsyncClient(
            timeout=self._timeout, headers=self._headers()
        ) as client:
            resp = await client.get(_FACTS_URL.format(cik=cik))
            resp.raise_for_status()
            payload = resp.json()
        return self._parse_facts(payload, ticker)

    async def health_check(self) -> dict[str, str]:
        try:
            mapping = await self._load_cik_map()
            return {"status": "ok", "detail": f"edgar OK — {len(mapping)} CIKs"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "detail": type(exc).__name__}
