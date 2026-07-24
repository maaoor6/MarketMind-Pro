"""Manual trading operations — the Telegram→Orchestrator control channel.

The Telegram cockpit writes manual override requests (flatten-all, close a
ticker) as Redis flags; the Orchestrator drains them at the top of its next
cycle, executes forced full-exits (reusing ``RiskManager.validate_sell``),
writes an acknowledgement, and pushes a confirmation. This is the institutional
"kill / de-risk now" control — it bypasses the anti-churn holding gates because
the human is explicitly overriding.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.database.cache import cache
from src.utils.logger import get_logger
from src.utils.timezone_utils import now_utc

logger = get_logger(__name__)

FLATTEN_KEY = "trading:manual:flatten"
CLOSE_KEY = "trading:manual:close"  # JSON list of tickers to close
ACK_KEY = "trading:manual:ack"
_TTL = 86400


@dataclass
class ManualOps:
    """Pending manual overrides drained for one cycle."""

    flatten: bool = False
    close: list[str] = field(default_factory=list)

    def any(self) -> bool:
        return self.flatten or bool(self.close)


async def request_flatten() -> None:
    """Queue a flatten-all (close every position next cycle)."""
    await cache.set(FLATTEN_KEY, "on", ttl=_TTL)


async def request_close(ticker: str) -> None:
    """Queue a single-ticker close for the next cycle (deduplicated)."""
    try:
        pending = await cache.get(CLOSE_KEY)
    except Exception:  # noqa: BLE001
        pending = None
    tickers = pending if isinstance(pending, list) else []
    t = ticker.strip().upper()
    if t and t not in tickers:
        tickers.append(t)
    await cache.set(CLOSE_KEY, tickers, ttl=_TTL)


async def pop_pending() -> ManualOps:
    """Read and clear all pending manual ops (called by the Orchestrator)."""
    ops = ManualOps()
    try:
        if await cache.get(FLATTEN_KEY) == "on":
            ops.flatten = True
            await cache.delete(FLATTEN_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.debug("manual_flatten_read_failed", error=str(exc))
    try:
        pending = await cache.get(CLOSE_KEY)
        if isinstance(pending, list) and pending:
            ops.close = [str(t).upper() for t in pending]
            await cache.delete(CLOSE_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.debug("manual_close_read_failed", error=str(exc))
    return ops


async def write_ack(summary: str) -> None:
    """Record that a manual op was applied (read back by the cockpit)."""
    try:
        await cache.set(
            ACK_KEY, {"summary": summary, "ts": now_utc().isoformat()}, ttl=_TTL
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("manual_ack_write_failed", error=str(exc))


async def read_ack() -> dict | None:
    """The most recent manual-op acknowledgement, if any."""
    try:
        ack = await cache.get(ACK_KEY)
    except Exception:  # noqa: BLE001
        return None
    return ack if isinstance(ack, dict) else None
