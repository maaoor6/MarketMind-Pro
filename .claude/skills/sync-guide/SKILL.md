---
name: sync-guide
allowed-tools: Read Grep Glob Write Bash
description: Scan the entire MarketMind-Pro codebase and update INTERNAL_CONTROL_PANEL.md, CLAUDE.md, and README.md to reflect all current commands, scripts, entry points, and architecture. Use when the user asks to update the operational guide or when new features are added.
---

# Sync Guide — Full Documentation Sync

Update all three documentation files to reflect the current state of the codebase.

## Phase 0 — Initialize

Before doing anything else, read the current state of all documentation and key project files:

**Documentation baseline:**
1. **Read** `INTERNAL_CONTROL_PANEL.md` — existing structure, sections, and command tables
2. **Read** `CLAUDE.md` — current architecture documentation and coding rules
3. **Read** `README.md` — current public-facing documentation

**Project structure baseline:**
4. **Read** `docker-compose.yml` — which services exist, their commands and ports
5. **Read** `pyproject.toml` — pytest config, tool settings, entry points
6. **Read** `requirements.txt` — installed dependencies
7. **Glob** `src/**/*.py` — map all existing modules and agents

This gives a full baseline of both what is documented and what actually exists in the codebase. Only update what has changed — never rewrite from scratch.

**Security check during Phase 0:** While reading these files, flag immediately if any file contains hardcoded secrets (tokens, passwords, API key values). Do not copy those values into any documentation file.

## Phase 1 — Discovery

Use these tools to find every executable script, entry point, and CLI command:

1. **Glob** — find all Python scripts, shell scripts, entry points:
   - `src/**/*.py` — look for `if __name__ == "__main__"`
   - `scripts/**/*.sh` — shell scripts
   - `*.toml`, `*.cfg` — package manager entry points
2. **Grep** — search for:
   - `@app.command`, `argparse`, `click` — CLI definitions
   - `ApplicationBuilder`, `run_polling` — Telegram bot entry points
   - `@router.post`, `@router.get` — MCP/FastAPI endpoints
   - `run_daily`, `run_repeating` — scheduled jobs
3. **Read** — read `docker-compose.yml`, `pyproject.toml`, `alembic.ini`

## Phase 2 — Verification

Use Bash to confirm syntax and help flags where possible:

```bash
python -m src.mcp.google_search_mcp --help 2>&1 || true
python -m src.mcp.sql_mcp_server --help 2>&1 || true
alembic --help 2>&1 | head -20
streamlit run --help 2>&1 | head -20
pytest --help 2>&1 | head -30
```

## Phase 3 — Update Files

### 1. `INTERNAL_CONTROL_PANEL.md` (detailed operational runbook)
- Update every command table: Command, Responsibility, How it works, Access & Flags, Success Check
- Add new commands/endpoints discovered in Phase 1
- Remove commands that no longer exist
- Write in English throughout

### 2. `CLAUDE.md` (developer reference for Claude Code)
- Update the Architecture section if new agents or modules were added
- Update commands in the Common Commands section
- Update DB models table, Redis cache keys table if changed
- Keep the same concise technical style — English only

### 3. `README.md` (public-facing project overview)
- Update the "What It Does" capability table if features changed
- Update Telegram Bot Commands table
- Update Automated Reports schedule if changed
- Update Environment Variables table
- Keep it high-level and readable — no internal implementation details

## Phase 4 — Security Check (MANDATORY before writing)

Before writing anything to any file, verify the content does not contain:

| Type | Examples to block |
|------|-------------------|
| API Keys | `sk-...`, `AIza...`, `ghp_...`, any string that looks like a token |
| Passwords | Values of `password=`, `pass=`, `secret=` |
| Tokens | Variable values — write the variable NAME only, never the value |
| Connection strings | Replace real passwords: use `postgresql://user:password@localhost` |
| Private IPs / Hosts | Internal IP addresses or private hostnames |
| Chat IDs | Real values of `TELEGRAM_CHAT_ID` |
| User paths | Do not write `/Users/<username>/...` paths in public files (README.md, CLAUDE.md) |

**Golden rule:** Always write the variable NAME — never the VALUE.

```
✅ OK:      DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/dbname
❌ NOT OK:  DATABASE_URL=postgresql+asyncpg://marketmind:Xk9$mP2@192.168.1.5:5432/marketmind
```

If a sensitive value is found in the code during Phase 1 — **report it to the user** instead of writing it to any file.

## Rules
- All commands must be copy-paste ready
- Only update what actually changed — do not restructure working sections
- INTERNAL_CONTROL_PANEL.md → English | CLAUDE.md → English | README.md → English
