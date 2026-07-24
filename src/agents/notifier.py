"""Telegram notifier — instant pushes + batched digest + trade rationale.

Central send path for the trading cockpit. Replaces the ad-hoc
``telegram.Bot`` construction scattered in the agents with one classifier:

* **Actionable** events (fills, agent freezes, macro-regime flips, strategy
  quarantines, risk breaches, errors) push to the admin chat immediately.
* **Routine** events (no-trade cycles, minor weight shifts) accumulate in a
  Redis-backed queue and flush as one periodic **digest**, preventing alert
  fatigue.

All sends are best-effort and target the single admin (``TELEGRAM_CHAT_ID``).
An optional running ``bot`` (from the dispatcher's Application) can be injected
so pushes can later carry inline buttons; otherwise a lightweight Bot is built
from the token.
"""

from __future__ import annotations

import telegram
from telegram.constants import ParseMode

from src.database.cache import cache
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

DIGEST_KEY = "trading:digest:pending"
_DIGEST_TTL = 2 * 86400
_MAX_DIGEST_LINES = 100

# Event kinds that always push immediately (everything else is batched).
_INSTANT_KINDS = frozenset(
    {
        "fill",
        "freeze",
        "regime_flip",
        "quarantine",
        "risk",
        "error",
        "reconcile",
        "daily_pnl",
    }
)


class Notifier:
    """Routes trading events to instant push or the batched digest."""

    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
        bot: telegram.Bot | None = None,
    ) -> None:
        self._token = token if token is not None else settings.telegram_token
        self._chat_id = chat_id if chat_id is not None else settings.telegram_chat_id
        self._bot = bot

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._chat_id)

    @staticmethod
    def is_instant(kind: str) -> bool:
        """True if ``kind`` should push immediately rather than batch."""
        return kind in _INSTANT_KINDS

    @staticmethod
    def build_rationale(parts: list[str | None]) -> str:
        """Join non-empty rationale fragments into one ``a + b + c`` line."""
        return " + ".join(p for p in parts if p)

    async def send(self, text: str) -> bool:
        """Send an HTML message to the admin chat immediately (best-effort)."""
        if not self.enabled:
            return False
        try:
            bot = self._bot or telegram.Bot(self._token)
            await bot.send_message(
                chat_id=self._chat_id, text=text, parse_mode=ParseMode.HTML
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("notify_failed", error=str(exc))
            return False

    async def push(self, kind: str, text: str) -> None:
        """Route an event: instant kinds send now, the rest join the digest."""
        if self.is_instant(kind):
            await self.send(text)
        else:
            await self.queue_digest(text)

    async def queue_digest(self, line: str) -> None:
        """Append a routine line to the pending digest (capped)."""
        try:
            pending = await cache.get(DIGEST_KEY)
            lines = pending if isinstance(pending, list) else []
            lines.append(line)
            await cache.set(DIGEST_KEY, lines[-_MAX_DIGEST_LINES:], ttl=_DIGEST_TTL)
        except Exception as exc:  # noqa: BLE001
            logger.debug("digest_queue_failed", error=str(exc))

    async def flush_digest(self) -> bool:
        """Send the accumulated digest as one message and clear it."""
        try:
            pending = await cache.get(DIGEST_KEY)
        except Exception:  # noqa: BLE001
            pending = None
        lines = pending if isinstance(pending, list) else []
        if not lines:
            return False
        header = f"🗒️ <b>Activity digest</b> — {len(lines)} update(s)"
        body = "\n".join(f"• {line}" for line in lines)
        sent = await self.send(f"{header}\n{body}")
        if sent:
            try:
                await cache.delete(DIGEST_KEY)
            except Exception as exc:  # noqa: BLE001
                logger.debug("digest_clear_failed", error=str(exc))
        return sent
