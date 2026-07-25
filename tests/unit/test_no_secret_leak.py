"""Regression tests: secrets must never reach the logs (Phase 4.2).

Locks in the fix from commit f0f0e9e and the generic redaction processor.
"""

import pytest
from src.utils import redact
from src.utils.redact import redact_processor, redact_text, reset_secret_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_secret_cache()
    yield
    reset_secret_cache()


# ── Pattern-based ────────────────────────────────────────────────────────────


def test_masks_api_key_query_param():
    url = "https://api.stlouisfed.org/series?series_id=DGS10&api_key=abcd1234secret"
    out = redact_text(url)
    assert "abcd1234secret" not in out
    assert "api_key=***" in out


def test_masks_google_key_and_token_params():
    assert "SEKRET" not in redact_text("https://x/y?key=SEKRETVALUE&z=1")
    assert "TOK" not in redact_text("https://x/y?token=TOKabcdef")


def test_masks_exchangerate_path_key():
    out = redact_text("https://v6.exchangerate-api.com/v6/mykey12345/latest/USD")
    assert "mykey12345" not in out
    assert "/v6/***/" in out


def test_masks_bearer_token():
    out = redact_text("Authorization: Bearer abcdef123456token")
    assert "abcdef123456token" not in out
    assert "Bearer ***" in out


# ── Value-based ──────────────────────────────────────────────────────────────


def test_masks_configured_secret_value(monkeypatch):
    monkeypatch.setattr(redact.settings, "stock_arena_token", "SUPERSECRETTOKEN123")
    reset_secret_cache()
    out = redact_text("trade failed for token SUPERSECRETTOKEN123 at broker")
    assert "SUPERSECRETTOKEN123" not in out
    assert "***" in out


def test_masks_db_password(monkeypatch):
    monkeypatch.setattr(
        redact.settings,
        "database_url",
        "postgresql+asyncpg://user:hunter2pass@localhost:5432/db",
    )
    reset_secret_cache()
    out = redact_text("connect postgresql+asyncpg://user:hunter2pass@localhost:5432/db")
    assert "hunter2pass" not in out


def test_short_values_not_over_redacted(monkeypatch):
    # A short/empty secret must not blank out unrelated text.
    monkeypatch.setattr(redact.settings, "telegram_token", "abc")
    reset_secret_cache()
    assert redact_text("abc def") == "abc def"


# ── Processor (recursive) ────────────────────────────────────────────────────


def test_processor_scrubs_nested_event():
    event = {
        "event": "fetch_failed",
        "url": "https://x?api_key=LEAKME123",
        "meta": {"auth": "Bearer TOPSECRET99"},
        "items": ["clean", "https://y?token=NESTED12345"],
    }
    out = redact_processor(None, "info", event)
    assert "LEAKME123" not in out["url"]
    assert "TOPSECRET99" not in out["meta"]["auth"]
    assert "NESTED12345" not in out["items"][1]
    assert out["items"][0] == "clean"


def test_processor_leaves_nonstrings():
    event = {"count": 5, "ok": True, "ratio": 0.5}
    out = redact_processor(None, "info", event)
    assert out == event
