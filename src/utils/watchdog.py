"""Infrastructure watchdog — RAM / CPU / Redis health with OOM prevention.

A lightweight supervisory task that samples process memory and CPU and pings
Redis, then alerts (Telegram) and raises a ``system:degraded`` flag before an
OOM / Signal-9 kill can take the bot down silently. The orchestrator can read
that flag to shed load (pause new buys) until pressure clears.

Zero required new dependency: uses ``psutil`` when installed (best signal),
otherwise stdlib ``/proc`` fallbacks on Linux (the Docker runtime target), and
no-ops cleanly where neither is available.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field

from src.database.cache import cache
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

DEGRADED_KEY = "system:degraded"

try:  # optional, better metrics when present
    import psutil  # type: ignore

    _HAS_PSUTIL = True
except Exception:  # noqa: BLE001
    psutil = None  # type: ignore
    _HAS_PSUTIL = False


@dataclass
class WatchdogReport:
    """One health sample."""

    mem_pct: float | None
    cpu_pct: float | None
    redis_ok: bool
    healthy: bool
    alerts: list[str] = field(default_factory=list)


def evaluate(
    mem_pct: float | None,
    cpu_pct: float | None,
    redis_ok: bool,
    mem_max: float,
    cpu_max: float,
) -> WatchdogReport:
    """Pure threshold evaluation → a report (fail-open on missing metrics)."""
    alerts: list[str] = []
    if mem_pct is not None and mem_pct >= mem_max:
        alerts.append(f"memory {mem_pct:.0f}% ≥ {mem_max:.0f}%")
    if cpu_pct is not None and cpu_pct >= cpu_max:
        alerts.append(f"cpu {cpu_pct:.0f}% ≥ {cpu_max:.0f}%")
    if not redis_ok:
        alerts.append("redis unreachable")
    return WatchdogReport(
        mem_pct=mem_pct,
        cpu_pct=cpu_pct,
        redis_ok=redis_ok,
        healthy=not alerts,
        alerts=alerts,
    )


# ── Metric readers (best-effort, None when unavailable) ──────────────────────


def read_mem_pct() -> float | None:
    """Process RSS as a percentage of total system memory."""
    if _HAS_PSUTIL:
        try:
            return float(psutil.virtual_memory().percent)
        except Exception:  # noqa: BLE001
            return None
    # Linux /proc fallback: process RSS / total memory.
    try:
        with open("/proc/self/statm") as fh:
            rss_pages = int(fh.read().split()[1])
        page = os.sysconf("SC_PAGE_SIZE")
        rss = rss_pages * page
        total = 0
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) * 1024
                    break
        if total > 0:
            return rss / total * 100.0
    except Exception:  # noqa: BLE001
        return None
    return None


def read_cpu_pct() -> float | None:
    """Approximate CPU load as a percentage of capacity."""
    if _HAS_PSUTIL:
        try:
            return float(psutil.cpu_percent(interval=None))
        except Exception:  # noqa: BLE001
            return None
    # Fallback: 1-minute load average normalized by CPU count.
    try:
        load1 = os.getloadavg()[0]
        cpus = os.cpu_count() or 1
        return min(100.0, load1 / cpus * 100.0)
    except Exception:  # noqa: BLE001
        return None


async def _redis_ok() -> bool:
    try:
        await cache.get(DEGRADED_KEY)  # any round-trip proves connectivity
        return True
    except Exception:  # noqa: BLE001
        return False


class Watchdog:
    """Periodic resource supervisor; alerts + flags degraded before OOM."""

    def __init__(self, notifier=None) -> None:
        self._notifier = notifier
        self._running = False

    async def check_once(self) -> WatchdogReport:
        report = evaluate(
            read_mem_pct(),
            read_cpu_pct(),
            await _redis_ok(),
            settings.watchdog_mem_pct_max,
            settings.watchdog_cpu_pct_max,
        )
        await self._apply(report)
        return report

    async def _apply(self, report: WatchdogReport) -> None:
        if report.healthy:
            try:
                await cache.delete(DEGRADED_KEY)
            except Exception:  # noqa: BLE001, S110
                pass
            return
        detail = ", ".join(report.alerts)
        logger.warning("watchdog_degraded", alerts=detail)
        try:
            await cache.set(DEGRADED_KEY, {"alerts": report.alerts}, ttl=300)
        except Exception:  # noqa: BLE001, S110
            pass
        if self._notifier is not None:
            await self._notifier.push("risk", f"⚠️ System pressure: {detail}")

    async def run(self) -> None:
        """Supervisory loop — never raises; safe as a background task."""
        if not settings.watchdog_enabled:
            logger.info("watchdog_disabled")
            return
        self._running = True
        logger.info("watchdog_started", psutil=_HAS_PSUTIL)
        while self._running:
            try:
                await self.check_once()
            except Exception as exc:  # noqa: BLE001 — supervisor must never die
                logger.debug("watchdog_check_failed", error=type(exc).__name__)
            await asyncio.sleep(settings.watchdog_check_seconds)

    def stop(self) -> None:
        self._running = False
