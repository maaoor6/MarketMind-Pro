"""Integration tests for PostgreSQL models and session management.

Requires Docker services running: docker-compose up -d postgres
Run with: pytest tests/integration/ -m integration
"""

from datetime import datetime
from decimal import Decimal

import pytest
import pytest_asyncio
import pytz

from src.database.models import Base, PriceHistory, UserAlert, DualListingGap
from src.database.session import async_engine, AsyncSessionLocal, check_db_connection


@pytest.mark.integration
@pytest.mark.asyncio
async def test_db_connection():
    result = await check_db_connection()
    assert result["status"] == "ok"


@pytest_asyncio.fixture
async def db_session():
    """Create a clean test session with rollback."""
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_insert_price_history(db_session):
    record = PriceHistory(
        ticker="TEVA",
        exchange="NYSE",
        timestamp=datetime(2024, 1, 15, 14, 30, tzinfo=pytz.UTC),
        timeframe="1d",
        open=Decimal("8.50"),
        high=Decimal("9.00"),
        low=Decimal("8.30"),
        close=Decimal("8.80"),
        volume=5_000_000,
    )
    db_session.add(record)
    await db_session.flush()
    assert record.id is not None
    assert record.ticker == "TEVA"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_insert_user_alert(db_session):
    alert = UserAlert(
        chat_id="123456789",
        ticker="AAPL",
        alert_type="PRICE_ABOVE",
        threshold=Decimal("200.00"),
    )
    db_session.add(alert)
    await db_session.flush()
    assert alert.id is not None
    assert alert.is_active is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_insert_dual_listing_gap(db_session):
    gap = DualListingGap(
        ticker_us="TEVA",
        ticker_tase="TEVA.TA",
        timestamp=datetime(2024, 1, 15, 14, 30, tzinfo=pytz.UTC),
        price_us_usd=Decimal("8.80"),
        price_tase_ils=Decimal("32.50"),
        usd_ils_rate=Decimal("3.700"),
        price_tase_in_usd=Decimal("8.7838"),
        gap_pct=Decimal("0.185"),
        gap_direction="US_PREMIUM",
    )
    db_session.add(gap)
    await db_session.flush()
    assert gap.id is not None
