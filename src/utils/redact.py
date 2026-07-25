"""Log redaction — a structlog processor that scrubs secrets from every event.

Belt-and-braces on top of the manual per-call-site hardening in ``f0f0e9e``:
even if new code logs a secret-bearing URL, a bearer token, or the raw value of
a configured secret, this processor masks it before it reaches any sink. Two
layers:

1. **Pattern-based** — query params (``?api_key=…``, ``?token=…``), ExchangeRate
   ``/v6/<key>/`` path segments, and ``Bearer <token>`` headers.
2. **Value-based** — the actual configured secret values (tokens/keys/DB
   password) are replaced wherever they appear.

Applied recursively to every string in the event dict (message + all fields).
"""

from __future__ import annotations

import re
from typing import Any

from src.utils.config import settings

_MASK = "***"

# Secret-bearing URL query params: ...?api_key=XXXX or &token=XXXX
_URL_PARAM_RE = re.compile(
    r'(?i)([?&](?:api[_-]?key|key|token|access[_-]?token|secret|apikey)=)[^&\s"\']+'
)
# ExchangeRate-API embeds the key in the path: /v6/<key>/latest
_EXRATE_RE = re.compile(r"(/v6/)[A-Za-z0-9]{8,}(/)")
# Authorization: Bearer <token>
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{6,}")

# Config fields that hold secrets (value-based redaction). DB URLs are handled
# specially (only the password segment is masked).
_SECRET_FIELDS = (
    "telegram_token",
    "stock_arena_token",
    "github_token",
    "fred_api_key",
    "google_api_key",
    "google_search_engine_id",
    "alpha_vantage_key",
    "exchangerate_api_key",
    "finnhub_api_key",
)

_MIN_SECRET_LEN = 6  # don't mask trivially short values (avoid over-redaction)

_cached_secrets: list[str] | None = None


def _db_password(url: str) -> str | None:
    """Extract the password from a SQLAlchemy URL, if present."""
    m = re.search(r"://[^:/@]+:([^@]+)@", url)
    return m.group(1) if m else None


def _secret_values() -> list[str]:
    """The set of raw secret strings to redact (cached, longest first)."""
    global _cached_secrets
    if _cached_secrets is not None:
        return _cached_secrets
    values: set[str] = set()
    for field in _SECRET_FIELDS:
        val = getattr(settings, field, "") or ""
        if isinstance(val, str) and len(val) >= _MIN_SECRET_LEN:
            values.add(val)
    for url_field in ("database_url", "database_url_sync"):
        pw = _db_password(getattr(settings, url_field, "") or "")
        if pw and len(pw) >= _MIN_SECRET_LEN:
            values.add(pw)
    # Longest first so overlapping secrets mask fully.
    _cached_secrets = sorted(values, key=len, reverse=True)
    return _cached_secrets


def reset_secret_cache() -> None:
    """Drop the cached secret set (tests / config reload)."""
    global _cached_secrets
    _cached_secrets = None


def redact_text(text: str) -> str:
    """Mask secret patterns and known secret values in a single string."""
    if not text:
        return text
    text = _URL_PARAM_RE.sub(rf"\1{_MASK}", text)
    text = _EXRATE_RE.sub(rf"\1{_MASK}\2", text)
    text = _BEARER_RE.sub(rf"\1{_MASK}", text)
    for secret in _secret_values():
        if secret in text:
            text = text.replace(secret, _MASK)
    return text


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_scrub(v) for v in value)
    return value


def redact_processor(logger: Any, method_name: str, event_dict: dict) -> dict:
    """structlog processor: recursively redact secrets from the event dict."""
    return {k: _scrub(v) for k, v in event_dict.items()}
