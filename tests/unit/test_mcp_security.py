"""Unit tests for MCP hardening — auth + SSRF host allowlist (Phase 4.3)."""

import pytest
from fastapi import HTTPException
from src.mcp import auth
from src.mcp.auth import host_allowed, require_mcp_auth

_ALLOWED = {"cnbc.com", "reuters.com", "sec.gov"}


# ── SSRF host allowlist ──────────────────────────────────────────────────────


def test_exact_domain_allowed():
    ok, host = host_allowed("https://cnbc.com/article", _ALLOWED)
    assert ok and host == "cnbc.com"


def test_subdomain_allowed():
    ok, _ = host_allowed("https://www.reuters.com/markets", _ALLOWED)
    assert ok


def test_substring_bypass_rejected():
    # The classic bypass that the old "allowed in domain" check permitted.
    ok, host = host_allowed("https://cnbc.com.evil.com/x", _ALLOWED)
    assert not ok
    assert host == "cnbc.com.evil.com"


def test_non_http_scheme_rejected():
    ok, _ = host_allowed("file:///etc/passwd", _ALLOWED)
    assert not ok
    ok2, _ = host_allowed("ftp://sec.gov/x", _ALLOWED)
    assert not ok2


def test_unlisted_domain_rejected():
    ok, _ = host_allowed("https://evil.com/x", _ALLOWED)
    assert not ok


def test_empty_url_rejected():
    ok, _ = host_allowed("", _ALLOWED)
    assert not ok


# ── MCP auth dependency ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auth_disabled_when_unset(monkeypatch):
    monkeypatch.setattr(auth.settings, "mcp_auth_token", "")
    # No exception → allowed (local-dev convenience).
    assert await require_mcp_auth(x_mcp_token=None) is None


@pytest.mark.asyncio
async def test_auth_accepts_correct_token(monkeypatch):
    monkeypatch.setattr(auth.settings, "mcp_auth_token", "s3cret")
    assert await require_mcp_auth(x_mcp_token="s3cret") is None


@pytest.mark.asyncio
async def test_auth_rejects_wrong_token(monkeypatch):
    monkeypatch.setattr(auth.settings, "mcp_auth_token", "s3cret")
    with pytest.raises(HTTPException) as exc:
        await require_mcp_auth(x_mcp_token="wrong")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_auth_rejects_missing_token(monkeypatch):
    monkeypatch.setattr(auth.settings, "mcp_auth_token", "s3cret")
    with pytest.raises(HTTPException):
        await require_mcp_auth(x_mcp_token=None)
