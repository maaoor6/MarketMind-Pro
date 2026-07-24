"""Unit tests for the Telegram Notifier (instant/digest routing) — no network."""

import pytest
from src.agents.notifier import DIGEST_KEY, Notifier


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, chat_id, text, parse_mode=None):
        self.sent.append(text)


class _FakeCache:
    def __init__(self) -> None:
        self.store: dict = {}

    async def get(self, k):
        return self.store.get(k)

    async def set(self, k, v, ttl=None):
        self.store[k] = v

    async def delete(self, k):
        self.store.pop(k, None)


# ── Pure classification / rationale ──────────────────────────────────────────


def test_instant_kinds():
    assert Notifier.is_instant("fill")
    assert Notifier.is_instant("quarantine")
    assert not Notifier.is_instant("no_trade")


def test_build_rationale_joins_nonempty():
    r = Notifier.build_rationale(["Breakout", None, "EDGAR clean", "", "corr OK"])
    assert r == "Breakout + EDGAR clean + corr OK"


def test_enabled_requires_token_and_chat():
    assert not Notifier(token="", chat_id="c").enabled
    assert not Notifier(token="t", chat_id="").enabled
    assert Notifier(token="t", chat_id="c").enabled


# ── Send / routing ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_uses_bot():
    bot = _FakeBot()
    n = Notifier(token="t", chat_id="c", bot=bot)
    assert await n.send("hello")
    assert bot.sent == ["hello"]


@pytest.mark.asyncio
async def test_send_disabled_noop():
    n = Notifier(token="", chat_id="")
    assert not await n.send("hi")


@pytest.mark.asyncio
async def test_push_instant_sends(monkeypatch):
    bot = _FakeBot()
    n = Notifier(token="t", chat_id="c", bot=bot)
    await n.push("fill", "BUY AAPL")
    assert bot.sent == ["BUY AAPL"]


@pytest.mark.asyncio
async def test_push_routine_queues(monkeypatch):
    fake = _FakeCache()
    monkeypatch.setattr("src.agents.notifier.cache", fake)
    bot = _FakeBot()
    n = Notifier(token="t", chat_id="c", bot=bot)
    await n.push("no_trade", "held AAPL")
    assert bot.sent == []  # not sent instantly
    assert fake.store[DIGEST_KEY] == ["held AAPL"]


@pytest.mark.asyncio
async def test_flush_digest_sends_and_clears(monkeypatch):
    fake = _FakeCache()
    monkeypatch.setattr("src.agents.notifier.cache", fake)
    bot = _FakeBot()
    n = Notifier(token="t", chat_id="c", bot=bot)
    await n.queue_digest("line A")
    await n.queue_digest("line B")
    assert await n.flush_digest()
    assert len(bot.sent) == 1
    assert "line A" in bot.sent[0] and "line B" in bot.sent[0]
    assert DIGEST_KEY not in fake.store  # cleared


@pytest.mark.asyncio
async def test_flush_empty_digest_noop(monkeypatch):
    fake = _FakeCache()
    monkeypatch.setattr("src.agents.notifier.cache", fake)
    n = Notifier(token="t", chat_id="c", bot=_FakeBot())
    assert not await n.flush_digest()
