"""Integration tests for PostgreSQL models and session management.

Requires Docker services running: docker-compose up -d postgres
Run with: pytest tests/integration/ -m integration
"""

from datetime import datetime
from decimal import Decimal

import pytest
import pytest_asyncio
import pytz
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.database.models import Base, DualListingGap, PriceHistory, UserAlert
from src.database.session import check_db_connection
from src.utils.config import settings


@pytest.mark.integration
@pytest.mark.asyncio
async def test_db_connection() -> None:
    result = await check_db_connection()
    assert result["status"] == "ok"


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """Create a fresh engine + session per test to avoid event loop conflicts."""
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        yield session
        await session.rollback()

    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_insert_price_history(db_session: AsyncSession) -> None:
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
async def test_insert_user_alert(db_session: AsyncSession) -> None:
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
async def test_insert_dual_listing_gap(db_session: AsyncSession) -> None:
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
