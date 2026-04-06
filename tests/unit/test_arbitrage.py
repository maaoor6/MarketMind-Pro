"""Unit tests for USD/ILS arbitrage calculations."""

from unittest.mock import AsyncMock, patch

import pytest
from src.quant.arbitrage import (
    calculate_arbitrage,
    format_arbitrage_message,
)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_arbitrage_us_premium():
    """US trades at premium over TASE equivalent."""
    # US: $10.00, TASE: ₪35.00 at rate 3.5 → TASE in USD = $10.00 → no gap
    # US: $11.00, TASE: ₪35.00 at rate 3.5 → TASE = $10.00 → gap = 10%
    with patch(
        "src.quant.arbitrage.get_usd_ils_rate", new_callable=AsyncMock, return_value=3.5
    ):
        signal = await calculate_arbitrage(
            ticker_us="TEVA",
            price_us_usd=11.0,
            price_tase_ils=35.0,
            usd_ils_rate=3.5,
        )
    assert signal.gap_direction == "US_PREMIUM"
    assert signal.gap_pct == pytest.approx(10.0, abs=0.01)
    assert signal.is_opportunity is True  # > 0.5%


@pytest.mark.unit
@pytest.mark.asyncio
async def test_arbitrage_tase_premium():
    """TASE trades at premium over US."""
    # US: $9.00, TASE: ₪35.00 @ 3.5 = $10.00 → TASE premium
    signal = await calculate_arbitrage(
        ticker_us="TEVA",
        price_us_usd=9.0,
        price_tase_ils=35.0,
        usd_ils_rate=3.5,
    )
    assert signal.gap_direction == "TASE_PREMIUM"
    assert signal.gap_pct > 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_arbitrage_no_gap():
    """Perfectly aligned prices → no opportunity."""
    # US: $10.00, TASE: ₪35.00 @ 3.5 = $10.00 exactly
    signal = await calculate_arbitrage(
        ticker_us="TEVA",
        price_us_usd=10.0,
        price_tase_ils=35.0,
        usd_ils_rate=3.5,
    )
    assert signal.gap_pct == pytest.approx(0.0, abs=0.01)
    assert signal.is_opportunity is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_arbitrage_zero_us_price_raises():
    with pytest.raises(ValueError, match="US price is zero"):
        await calculate_arbitrage(
            ticker_us="TEVA",
            price_us_usd=0.0,
            price_tase_ils=35.0,
            usd_ils_rate=3.5,
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_arbitrage_format_message():
    signal = await calculate_arbitrage(
        ticker_us="TEVA",
        price_us_usd=11.0,
        price_tase_ils=35.0,
        usd_ils_rate=3.5,
    )
    msg = format_arbitrage_message(signal)
    assert "TEVA" in msg
    assert "Gap:" in msg
    assert "OPPORTUNITY" in msg


@pytest.mark.unit
@pytest.mark.asyncio
async def test_arbitrage_min_gap_threshold():
    """Gap just below threshold is not flagged as opportunity."""
    # 0.4% gap — below MIN_GAP_PCT=0.5%
    us_price = 10.0
    tase_price_usd = us_price * 1.004  # 0.4% above
    signal = await calculate_arbitrage(
        ticker_us="NICE",
        price_us_usd=us_price,
        price_tase_ils=tase_price_usd * 3.5,
        usd_ils_rate=3.5,
    )
    assert signal.is_opportunity is False
