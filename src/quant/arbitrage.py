"""USD/ILS arbitrage detection for dual-listed stocks (TASE + NYSE/NASDAQ)."""

from dataclasses import dataclass

import httpx

from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Known dual-listed stocks: US ticker -> TASE ticker
DUAL_LISTED = {
    "TEVA": "TEVA.TA",
    "NICE": "NICE.TA",
    "CHKP": "CHKP.TA",
    "AMDOCS": "DOX.TA",
    "CEVA": "CEVA.TA",
    "GILT": "GILT.TA",
    "RADCOM": "RDCM.TA",
    "TOWER": "TSEM.TA",
    "ORCL": "ORCL.TA",
}

# Minimum gap threshold to flag as arbitrage opportunity
MIN_GAP_PCT = 0.5  # 0.5%


@dataclass
class ArbitrageSignal:
    ticker_us: str
    ticker_tase: str
    price_us_usd: float
    price_tase_ils: float
    usd_ils_rate: float
    price_tase_in_usd: float
    gap_pct: float
    gap_direction: str  # "US_PREMIUM" or "TASE_PREMIUM"
    is_opportunity: bool


async def get_usd_ils_rate() -> float:
    """Fetch live USD/ILS exchange rate.

    Uses ExchangeRate-API with fallback to a hardcoded approximate rate.
    """
    api_key = settings.exchangerate_api_key
    if not api_key:
        logger.warning("no_exchangerate_api_key", fallback_rate=3.72)
        return 3.72  # Approximate fallback

    url = f"https://v6.exchangerate-api.com/v6/{api_key}/pair/USD/ILS"
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            rate = float(data["conversion_rate"])
            logger.info("usd_ils_rate_fetched", rate=rate)
            return rate
        except Exception as exc:
            logger.error("usd_ils_rate_fetch_failed", error=str(exc))
            return 3.72


async def calculate_arbitrage(
    ticker_us: str,
    price_us_usd: float,
    price_tase_ils: float,
    usd_ils_rate: float | None = None,
) -> ArbitrageSignal:
    """Calculate arbitrage gap between US and TASE listing.

    Args:
        ticker_us: US ticker symbol (e.g., 'TEVA').
        price_us_usd: Current US price in USD.
        price_tase_ils: Current TASE price in ILS (agorot → divide by 100 if needed).
        usd_ils_rate: USD/ILS exchange rate. Fetched live if None.

    Returns:
        ArbitrageSignal with gap analysis.
    """
    ticker_tase = DUAL_LISTED.get(ticker_us, f"{ticker_us}.TA")

    if usd_ils_rate is None:
        usd_ils_rate = await get_usd_ils_rate()

    # TASE prices are often quoted in ILS (not agorot)
    price_tase_in_usd = price_tase_ils / usd_ils_rate

    if price_us_usd == 0:
        raise ValueError(f"US price is zero for {ticker_us}")

    gap_pct = ((price_us_usd - price_tase_in_usd) / price_tase_in_usd) * 100
    gap_direction = "US_PREMIUM" if gap_pct > 0 else "TASE_PREMIUM"

    signal = ArbitrageSignal(
        ticker_us=ticker_us,
        ticker_tase=ticker_tase,
        price_us_usd=price_us_usd,
        price_tase_ils=price_tase_ils,
        usd_ils_rate=usd_ils_rate,
        price_tase_in_usd=round(price_tase_in_usd, 4),
        gap_pct=round(abs(gap_pct), 4),
        gap_direction=gap_direction,
        is_opportunity=abs(gap_pct) >= MIN_GAP_PCT,
    )

    if signal.is_opportunity:
        logger.info(
            "arbitrage_opportunity",
            ticker=ticker_us,
            gap_pct=signal.gap_pct,
            direction=gap_direction,
        )

    return signal


def format_arbitrage_message(signal: ArbitrageSignal) -> str:
    """Format arbitrage signal as a Telegram message."""
    emoji = "🟢" if signal.is_opportunity else "⚪"
    direction_label = (
        "🇺🇸 US trades at PREMIUM"
        if signal.gap_direction == "US_PREMIUM"
        else "🇮🇱 TASE trades at PREMIUM"
    )

    return (
        f"{emoji} *Arbitrage — {signal.ticker_us} / {signal.ticker_tase}*\n"
        f"US Price: ${signal.price_us_usd:,.3f}\n"
        f"TASE Price: ₪{signal.price_tase_ils:,.3f} (${signal.price_tase_in_usd:,.3f})\n"
        f"USD/ILS Rate: {signal.usd_ils_rate:.4f}\n"
        f"Gap: {signal.gap_pct:.2f}% — {direction_label}\n"
        + ("⚡ *OPPORTUNITY DETECTED*" if signal.is_opportunity else "")
    )
