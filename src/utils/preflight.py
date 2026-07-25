"""Boot-time readiness self-check.

Runs once at startup: verifies the required secrets are present, Redis is
reachable, the data-provider registry builds, and (best-effort) the DB answers.
Emits a redacted readiness report so a misconfiguration is obvious in the logs
instead of surfacing as a cryptic failure mid-cycle. Non-critical gaps are
warnings; the app still boots (fail-open) — only genuinely critical misconfig
(no Telegram credentials) is flagged loudly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class PreflightItem:
    name: str
    ok: bool
    critical: bool = False
    detail: str = ""


@dataclass
class PreflightReport:
    items: list[PreflightItem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True unless a critical check failed."""
        return all(i.ok for i in self.items if i.critical)

    @property
    def failures(self) -> list[PreflightItem]:
        return [i for i in self.items if not i.ok]


def evaluate(items: list[PreflightItem]) -> PreflightReport:
    """Pure assembly of a report from checked items."""
    return PreflightReport(items=list(items))


async def _redis_ok() -> bool:
    try:
        from src.database.cache import cache

        await cache.get("system:preflight")
        return True
    except Exception:  # noqa: BLE001
        return False


async def _db_ok() -> bool:
    try:
        from sqlalchemy import text

        from src.database.session import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


def _providers_ok() -> bool:
    try:
        from src.data import get_provider

        return bool(get_provider().providers)
    except Exception:  # noqa: BLE001
        return False


async def run_preflight() -> PreflightReport:
    """Gather all readiness checks, log a redacted report, and return it."""
    items: list[PreflightItem] = [
        PreflightItem(
            "telegram_credentials",
            bool(settings.telegram_token and settings.telegram_chat_id),
            critical=True,
            detail="TELEGRAM_TOKEN + TELEGRAM_CHAT_ID",
        ),
        PreflightItem("redis", await _redis_ok(), critical=True),
        PreflightItem("database", await _db_ok(), critical=False),
        PreflightItem("data_providers", _providers_ok(), critical=True),
    ]

    # Trading credentials matter only when trading is actually enabled.
    if settings.trading_enabled:
        items.append(
            PreflightItem(
                "stockarena_token",
                bool(settings.stock_arena_token),
                critical=True,
                detail="required because TRADING_ENABLED=true",
            )
        )
    # MCP auth is a soft recommendation (warn, don't block).
    items.append(
        PreflightItem(
            "mcp_auth_token",
            bool(settings.mcp_auth_token),
            critical=False,
            detail="unset → MCP /tools endpoints are unauthenticated",
        )
    )

    report = evaluate(items)
    for item in report.items:
        level = "ok" if item.ok else ("error" if item.critical else "warning")
        (logger.info if item.ok else logger.warning)(
            "preflight_check", check=item.name, status=level, detail=item.detail
        )
    if report.ok:
        logger.info("preflight_passed", checks=len(report.items))
    else:
        logger.error(
            "preflight_failed_critical",
            failures=[i.name for i in report.failures if i.critical],
        )
    return report
