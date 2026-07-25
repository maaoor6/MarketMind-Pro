"""Shared security helpers for the MCP HTTP servers.

* ``require_mcp_auth`` — a FastAPI dependency guarding the ``/tools/*``
  endpoints with a shared ``X-MCP-Token`` header (``settings.mcp_auth_token``).
  When the token is unset the guard is disabled (local-dev convenience) but a
  warning is logged so it is never silently open in production.
* ``host_allowed`` — a strict SSRF allowlist check that parses the URL host and
  matches it as an exact domain or a proper subdomain, fixing the substring
  bypass (``cnbc.com.evil.com``) in the old ``allowed in domain`` check.
"""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import Header, HTTPException

from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


async def require_mcp_auth(x_mcp_token: str | None = Header(default=None)) -> None:
    """Reject requests without the shared MCP token (no-op when unset)."""
    expected = settings.mcp_auth_token
    if not expected:
        logger.warning("mcp_auth_disabled")  # no token configured
        return
    if x_mcp_token != expected:
        raise HTTPException(status_code=401, detail="unauthorized")


def host_allowed(url: str, allowed: set[str]) -> tuple[bool, str]:
    """Return ``(is_allowed, host)`` for an http(s) URL against an allowlist.

    A host matches only if it equals an allowed domain or is a proper subdomain
    of one (``host == a or host.endswith("." + a)``). Non-http(s) schemes are
    always rejected.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, ""
    host = (parsed.hostname or "").lower()
    if not host:
        return False, ""
    ok = any(host == a or host.endswith(f".{a}") for a in allowed)
    return ok, host
