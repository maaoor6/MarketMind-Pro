"""Database session factory and engine configuration."""

from collections.abc import AsyncGenerator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

# ── Async engine (FastAPI, agents) ───────────────────────────────────
async_engine: AsyncEngine = create_async_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    echo=(settings.app_env == "development"),
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

# ── Sync engine (Alembic migrations, scripts) ────────────────────────
sync_engine = create_engine(
    settings.database_url_sync,
    pool_pre_ping=True,
    echo=False,
)

SyncSessionLocal = sessionmaker(
    bind=sync_engine,
    autoflush=False,
    autocommit=False,
)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a managed async DB session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


def get_sync_session() -> Session:
    """Return a sync session (for scripts/migrations). Caller must close."""
    return SyncSessionLocal()


async def check_db_connection() -> dict[str, str]:
    """Health check for the database connection."""
    try:
        from sqlalchemy import text

        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "ok", "detail": "PostgreSQL connected"}
    except Exception as exc:
        logger.error("db_connection_failed", error=str(exc))
        return {"status": "error", "detail": str(exc)}
