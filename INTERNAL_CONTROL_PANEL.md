# 🧠 INTERNAL_CONTROL_PANEL.md — MarketMind-Pro
> Internal developer runbook | Updated: April 2026

---

## 📋 Table of Contents

1. [Operational Runbook](#operational-runbook)
2. [What's New](#whats-new)
3. [Data Management](#data-management)
4. [🚨 Risk Management](#-risk-management)

---

## Operational Runbook

### 🚀 Start the Full System (Docker — recommended)

| Command | Responsibility | How it works | Success Check |
|---------|---------------|--------------|---------------|
| `docker compose up -d` | Start all services in the background | Starts: postgres, redis, app (bot + quant engine), dashboard, mcp-search, mcp-sql, migrate | `docker compose ps` — all services should show `Up (healthy)` |
| `docker compose up -d --build` | Rebuild + start after code changes | Runs `docker build` before starting — ensures updated code is inside the container | `docker compose logs -f app` — no import errors |
| `docker compose down` | Stop all services | Stops all containers, data is preserved in volumes | `docker compose ps` — no active services |
| `docker compose down -v` | ⚠️ **Full wipe** — stop + delete volumes | Deletes all data: PostgreSQL, Redis | Warning: **all history is lost** |
| `docker compose ps` | Check service status | Shows status, ports, and health for every service | All services should be: `Up (healthy)` |
| `docker compose logs -f app` | Stream live logs | Real-time log stream for bot + quant engine | Logs appear as structured JSON — no CRITICAL `ERROR` |
| `docker compose logs -f [service]` | Logs for a specific service | Replace `[service]` with: `postgres`, `redis`, `dashboard`, `mcp-search`, `mcp-sql` | — |

---

### 🤖 Main Application

#### `python -m src.main`

| Field | Details |
|-------|---------|
| **Command** | `python -m src.main` |
| **Responsibility** | Main entry point — starts all AI agents |
| **How it works** | 1. Initializes Redis connection. 2. Starts `QuantEngine` (polls watchlist every 60s). 3. Starts `TelegramDispatcher` (Telegram bot + scheduled jobs). 4. Handles SIGTERM/SIGINT for graceful shutdown. |
| **Access & Flags** | No flags — all configuration comes from `.env` |
| **Success Check** | In Telegram: send `/health` — all services should return ✅ |

---

### 📡 Telegram Bot Commands

#### `/start`

| Field | Details |
|-------|---------|
| **Command** | Send `/start` in Telegram |
| **Responsibility** | Welcome menu with exchange status |
| **How it works** | Returns NYSE status (open/closed), countdown to next market open, and an InlineKeyboard with quick-action buttons |
| **Access & Flags** | None |
| **Success Check** | Welcome message + buttons: `📊 Analyze Stock`, `🔍 Fibonacci`, `📰 News`, `💚 Health` |

---

#### `/analyze [TICKER]`

| Field | Details |
|-------|---------|
| **Command** | `/analyze AAPL` |
| **Responsibility** | Full institutional-grade analysis report for a stock |
| **How it works** | 1. `QuantEngine.analyze(ticker)` — fetches price data from yfinance (Redis cache 60s), computes RSI, MACD, moving averages, Fibonacci. 2. `fetch_company_profile()` — company profile from yfinance (4h cache). 3. `fetch_insider_transactions()` — insider trades. 4. `NewsSearchAgent.analyze_sentiment()` — sentiment from RSS (15min cache). 5. `publish_ticker_chart()` — publishes Plotly chart to GitHub Pages. 6. `_wait_for_pages()` — waits until the page is live (up to 60s). |
| **Access & Flags** | `TICKER` — required (e.g. `AAPL`, `TEVA`, `NVDA`) |
| **Success Check** | Message returned with: price, RSI, MACD, Fibonacci, P/E, EPS, insider trades, news sentiment, `📊 Interactive Chart` button |

---

#### `/news [TICKER]`

| Field | Details |
|-------|---------|
| **Command** | `/news AAPL` or `/news` (no ticker) |
| **Responsibility** | Top 5 live news headlines with snippets; global market snapshot if no ticker given |
| **How it works** | With ticker: `NewsSearchAgent` fetches headlines from Google News RSS + Yahoo Finance. Without ticker: fetches prices for 15 ETFs (SPY, VOO, QQQ, DIA, IWM, RSP, DX-Y.NYB, ^VIX, TLT, AGG, GLD, SLV, USO, BTC-USD, ETH-USD) with % change. |
| **Access & Flags** | `[TICKER]` — optional |
| **Success Check** | With ticker: 5 headlines + sources. Without ticker: snapshot grouped by category (Equities, Currency/Vol, Fixed Income, Commodities, Crypto) |

---

#### `/fibonacci [TICKER]`

| Field | Details |
|-------|---------|
| **Command** | `/fibonacci TEVA` |
| **Responsibility** | Fibonacci levels from 52-week high/low |
| **How it works** | `calculate_fibonacci()` takes 252 trading days of data, computes retracement levels (0%, 23.6%, 38.2%, 50%, 61.8%, 78.6%, 100%) and extension levels (127.2%, 161.8%, 261.8%), determines trend direction from the last 20 days |
| **Access & Flags** | `TICKER` — required |
| **Success Check** | Nearest support level (🟢) and nearest resistance level (🔴) are displayed |

---

#### `/compare [T1] [T2]`

| Field | Details |
|-------|---------|
| **Command** | `/compare AAPL MSFT` |
| **Responsibility** | Side-by-side comparison of two stocks |
| **How it works** | Runs `QuantEngine.analyze()` and `fetch_company_profile()` concurrently for both tickers, builds an HTML table with: price, % change, RSI, MACD, Fibonacci trend, P/E, EPS, market cap, sentiment |
| **Access & Flags** | `T1 T2` — two tickers, both required |
| **Success Check** | Comparison table with two columns — one per metric |

---

#### `/health`

| Field | Details |
|-------|---------|
| **Command** | `/health` |
| **Responsibility** | Full system health dashboard |
| **How it works** | Checks concurrently: 1. PostgreSQL — `SELECT 1`. 2. Redis — `PING`. 3. MCP Search Server — `GET localhost:8001/health`. 4. MCP SQL Server — `GET localhost:8002/health`. 5. Google News RSS — `HEAD` request. 6. `QuantEngine.health_check()`. |
| **Access & Flags** | None |
| **Success Check** | ✅ for every service. ⚠️ = warning, ❌ = failure |

---

### ⏰ Scheduled Jobs (APScheduler)

| Trigger | Responsibility | How it works | Access & Flags | Success Check |
|---------|---------------|--------------|----------------|---------------|
| **9:00 AM ET, Mon–Fri** | Pre-market report | `_job_market_preview()` — analyzes: AAPL, MSFT, NVDA, SPY, QQQ with RSI + global snapshot | Automatic — no parameters | Telegram message with header "🌅 Pre-Market Preview" |
| **4:15 PM ET, Mon–Fri** | Market close report | `_job_market_close_regular()` — analyzes: AAPL, MSFT, NVDA, GOOGL, SPY with full signals | Automatic — no parameters | Telegram message with header "📊 Market Close Summary" |

---

### 📊 Streamlit Dashboard

#### `streamlit run src/ui/dashboard.py`

| Field | Details |
|-------|---------|
| **Command** | `streamlit run src/ui/dashboard.py` |
| **Port** | http://localhost:8501 |
| **Responsibility** | Local browser UI for ad-hoc analysis without Telegram |
| **How it works** | Sidebar: ticker, period (3mo–5y), moving average selection, Fibonacci toggle. Click "Analyze" → `QuantEngine.fetch_price_data()` → 3-panel Plotly chart: candlestick+MA, volume, RSI + Fibonacci tables |
| **Access & Flags** | `--server.port=PORT` — change port. `--server.address=0.0.0.0` — expose to local network |
| **Success Check** | Browser opens at http://localhost:8501, select AAPL, click Analyze — chart appears |

---

### 🌐 MCP Servers

#### `python -m src.mcp.google_search_mcp`

| Field | Details |
|-------|---------|
| **Command** | `python -m src.mcp.google_search_mcp` |
| **Command (stdio)** | `python -m src.mcp.google_search_mcp --stdio` |
| **Port** | 8001 |
| **Responsibility** | HTTP + MCP server for web search used by AI agents |
| **How it works** | Without `--stdio`: FastAPI on port 8001. With `--stdio`: MCP server for clients like Claude Desktop. Provides: `search_web`, `scrape_page`, `search_financial_news` |
| **Access & Flags** | `--stdio` — stdio mode (optional). Requires: `GOOGLE_API_KEY`, `GOOGLE_SEARCH_ENGINE_ID` in `.env` |
| **Success Check** | `curl http://localhost:8001/health` → `{"status": "ok"}` |

**Available endpoints:**

```bash
# Health check
curl http://localhost:8001/health

# Web search
curl -X POST http://localhost:8001/tools/search_web \
  -H "Content-Type: application/json" \
  -d '{"query": "AAPL earnings", "num_results": 5}'

# Financial news search
curl -X POST http://localhost:8001/tools/search_financial_news \
  -H "Content-Type: application/json" \
  -d '{"ticker": "AAPL", "language": "en"}'

# Scrape a web page
curl -X POST http://localhost:8001/tools/scrape_page \
  -H "Content-Type: application/json" \
  -d '{"url": "https://finance.yahoo.com/quote/AAPL"}'
```

---

#### `python -m src.mcp.sql_mcp_server`

| Field | Details |
|-------|---------|
| **Command** | `python -m src.mcp.sql_mcp_server` |
| **Command (stdio)** | `python -m src.mcp.sql_mcp_server --stdio` |
| **Port** | 8002 |
| **Responsibility** | HTTP + MCP server for PostgreSQL queries used by AI agents |
| **How it works** | Without `--stdio`: FastAPI on port 8002. With `--stdio`: MCP server. Provides protected access to: PriceHistory, DualListingGap, UserAlert, SentimentRecord |
| **Access & Flags** | `--stdio` — stdio mode (optional). Requires `DATABASE_URL` in `.env` |
| **Success Check** | `curl http://localhost:8002/health` → `{"status": "ok"}` |

**Available endpoints:**

```bash
# Health check
curl http://localhost:8002/health

# Price history
curl -X POST http://localhost:8002/tools/query_prices \
  -H "Content-Type: application/json" \
  -d '{"ticker": "AAPL", "from_date": "2024-01-01", "limit": 30}'

# Arbitrage history
curl -X POST http://localhost:8002/tools/get_arbitrage_history \
  -H "Content-Type: application/json" \
  -d '{"ticker_us": "TEVA", "min_gap_pct": 0.5, "limit": 10}'

# User alerts
curl -X POST http://localhost:8002/tools/get_alerts \
  -H "Content-Type: application/json" \
  -d '{"chat_id": "123456789"}'

# Sentiment history
curl -X POST http://localhost:8002/tools/get_sentiment_history \
  -H "Content-Type: application/json" \
  -d '{"ticker": "NVDA", "limit": 20}'

# Volume spikes
curl -X POST http://localhost:8002/tools/get_volume_spikes \
  -H "Content-Type: application/json" \
  -d '{"ticker": "TSLA", "limit": 10}'
```

---

### 🗄️ Database (Alembic)

| Command | Responsibility | How it works | Access & Flags | Success Check |
|---------|---------------|--------------|----------------|---------------|
| `alembic upgrade head` | Apply all pending migrations | Reads `DATABASE_URL_SYNC` from `.env`, applies migrations from `alembic/versions/`, creates tables + indexes | `head` — latest version. Specific version: `alembic upgrade abc123` | `alembic current` — shows active version |
| `alembic revision --autogenerate -m "description"` | Create a new migration | Compares models in `src/database/models.py` to DB schema, generates migration file | `-m "description"` — required | New file appears in `alembic/versions/` |
| `alembic downgrade -1` | Roll back one migration | Runs the `downgrade()` function of the current migration | `-1` — one step back. `base` — to beginning | `alembic current` — shows previous version |
| `alembic history` | View migration history | Prints all migrations in order | `--verbose` — full details | Sorted list of revisions |
| `docker compose run --rm migrate` | Run migrations via Docker | Runs a one-off container with `alembic upgrade head` | — | Exits with code 0 |

**DB management commands:**
```bash
# Open PostgreSQL shell
docker compose exec postgres psql -U marketmind -d marketmind

# Direct query
docker compose exec postgres psql -U marketmind -d marketmind -c "SELECT COUNT(*) FROM price_history;"

# Backup database
docker compose exec postgres pg_dump -U marketmind marketmind > backup_$(date +%Y%m%d).sql

# Restore database
docker compose exec -T postgres psql -U marketmind marketmind < backup_20260101.sql
```

---

### 🧪 Testing (pytest)

| Command | Responsibility | How it works | Access & Flags | Success Check |
|---------|---------------|--------------|----------------|---------------|
| `pytest tests/unit/ -v` | Run all 47 unit tests | No I/O — does not require Docker | `-v` verbose. `-x` stop on first failure | `47 passed` |
| `pytest tests/unit/ --cov=src/quant --cov-report=term-missing` | Tests + coverage report | Computes coverage on `src/quant/` — must be ≥80% | `--cov-report=html` — HTML report in `htmlcov/` | `TOTAL ... 80%+` |
| `pytest tests/integration/ -m integration -v` | Integration tests (requires Docker) | Connects to PostgreSQL, creates tables, performs CRUD | Requires: `docker compose up -d postgres redis` | `4 passed` |
| `pytest tests/unit/test_indicators.py -v` | Run a specific test file | Runs only indicator tests | — | `X passed` |
| `pytest tests/unit/test_indicators.py::test_rsi_known_values -v` | Run a single test | Runs one test by name | — | `1 passed` |
| `pytest tests/unit/ -m "not slow"` | All tests except slow ones | Filters by marker | — | — |

**Test files:**
```
tests/unit/
├── test_indicators.py      — RSI, MACD, SMA, EMA, volume spike
├── test_fibonacci.py       — Fibonacci levels, support/resistance
├── test_arbitrage.py       — TASE/NYSE gap calculation
└── test_timezone_utils.py  — market hours, timezone helpers

tests/integration/
└── test_database.py        — PostgreSQL models (PriceHistory, UserAlert, DualListingGap)
```

---

### 🔍 Code Quality & Security

| Command | Responsibility | How it works | Access & Flags | Success Check |
|---------|---------------|--------------|----------------|---------------|
| `ruff check src/ tests/` | Lint + style + isort | Checks pycodestyle, pyflakes, imports, naming, security | `--fix` auto-fix. `--select E501` specific rule | `All checks passed` |
| `black src/ tests/` | Auto-format | Formats code to Black 88-char standard | `--check` check only. `--diff` show diff | `All done! ✨` |
| `black --check src/ tests/` | Check if formatting is needed | Non-zero exit if code is unformatted | — | `All done! ✨ 🍰 ✨` |
| `bandit -r src/ -ll` | Security scan | Checks for MEDIUM+ severity findings | `-ll` LOW and above. `-lll` HIGH only | `No issues identified.` |
| `bash scripts/install_hooks.sh` | Install pre-commit hooks | Creates `.git/hooks/pre-commit` with: ruff → black → bandit → pytest | — | Hook runs on `git commit` |

**Pre-Commit Hook order:**
```
1. ruff check src/ tests/     ← lint
2. black --check src/ tests/  ← format
3. bandit -r src/ -ll         ← security
4. pytest tests/unit/ -x -q   ← unit tests
```

---

### 🛠️ Local Development (without Docker)

```bash
# Set up virtual environment
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Copy config
cp .env.example .env
# ✏️ Edit .env with TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, GITHUB_TOKEN

# Start infrastructure only
docker compose up -d postgres redis

# Apply migrations
alembic upgrade head

# Run the full application
python -m src.main

# Run bot only (without QuantEngine)
python -m src.agents.telegram_dispatcher

# Run dashboard
streamlit run src/ui/dashboard.py
```

---

### 🔧 Quick Maintenance Commands

```bash
# Flush Redis cache
docker compose exec redis redis-cli FLUSHDB

# View cache keys
docker compose exec redis redis-cli KEYS "quote:*"
docker compose exec redis redis-cli KEYS "sentiment:*"
docker compose exec redis redis-cli KEYS "fundamentals:*"

# TTL of a specific key
docker compose exec redis redis-cli TTL "quote:AAPL:1d"

# Row counts per table
docker compose exec postgres psql -U marketmind -d marketmind \
  -c "SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup DESC;"

# Rebuild a single service
docker compose up -d --build app

# Restart a single service
docker compose restart app

# Python version inside container
docker compose exec app python --version
```

---

## What's New

### Version 1.1.0 (April 2026)

| Feature | Description |
|---------|-------------|
| **GitHub Pages Polling** | Bot waits up to 60s after chart publish — user receives a live link ✅ |
| **defusedxml** | Replaced `xml.etree.ElementTree` with `defusedxml` — prevents XXE attacks |
| **ETF Snapshot** | Market snapshot with 15 ETFs (SPY, QQQ, TLT, GLD...) — no raw futures |
| **ET Timestamps** | "Updated:" time shown in ET (not UTC) across all reports |
| **News RSS Fallback** | Fallback chain: MCP → Google Search API → **Google News RSS + Yahoo Finance** (free) |
| **`cmd_fallback`** | Unrecognized messages return a quick-action inline keyboard |
| **`_wait_for_pages()`** | HEAD poll every 4s — 60s timeout |
| **Integration Tests Fix** | Each test creates its own engine — no more `Event loop is closed` error |
| **`/health` News Check** | Tests Google News RSS reachability inside `/health` |

---

## Data Management

### PostgreSQL Models

| Table | Key Fields | Usage |
|-------|-----------|-------|
| `price_history` | ticker, exchange, timestamp, timeframe, OHLCV | Historical price data from QuantEngine |
| `dual_listing_gap` | ticker_us, ticker_tase, gap_pct, gap_direction | TASE/NYSE arbitrage gaps |
| `user_alert` | chat_id, ticker, alert_type, threshold, is_active | Price/RSI/volume alerts |
| `insider_transaction` | ticker, insider_name, transaction_date, shares, total_value | Insider trades |
| `sentiment_record` | ticker, timestamp, score, headline_count, sources (JSON) | News sentiment history |

### Redis Cache Keys

| Key | TTL | Source |
|-----|-----|--------|
| `quote:{ticker}:{timeframe}` | 60s (default) | QuantEngine |
| `sentiment:{ticker}` | 900s (15 min) | NewsSearchAgent |
| `fundamentals:{ticker}` | 14400s (4 hours) | fundamentals.py |

> ⚠️ Empty results are **not cached** — always retried.

### Dual-Listed Stocks (TASE + NYSE)

| NYSE | TASE |
|------|------|
| TEVA | TEVA.TA |
| NICE | NICE.TA |
| CHKP | CHKP.TA |
| AMDOCS | DOX.TA |
| CEVA | CEVA.TA |
| GILT | GILT.TA |
| RADCOM | RDCM.TA |
| TOWER | TSEM.TA |
| ORCL | ORCL.TA |

### Market Snapshot — Categories

| Category | Symbols |
|----------|---------|
| Equities | SPY, VOO, QQQ, DIA, IWM, RSP |
| Currency/Vol | DX-Y.NYB (DXY), ^VIX |
| Fixed Income | TLT, AGG |
| Commodities | GLD, SLV, USO |
| Crypto | BTC-USD, ETH-USD |

---

## 🚨 Risk Management

### Common Failures & Solutions

| Symptom | Likely Cause | Solution |
|---------|-------------|---------|
| `/analyze` not responding | Redis down / yfinance timeout | `/health` → check Redis. `docker compose restart redis` |
| Chart 404 after publish | GitHub Pages CDN still propagating | `_wait_for_pages()` handles this automatically up to 60s |
| "Event loop is closed" in tests | Shared engine across tests | Each `db_session` fixture must create its own engine |
| `bandit -r src/` fails | B314 — unsafe XML parsing | Use `safe_fromstring` from `defusedxml` |
| Ruff N817/N813 on import | CamelCase alias | Import the function directly: `from defusedxml.ElementTree import fromstring as safe_fromstring` |
| `git push` rejected (non-fast-forward) | Bot committed to remote | `git stash && git pull --rebase && git stash pop && git push` |
| Empty news results | Google News RSS returns 302 | Ensure `follow_redirects=True` on httpx client |
| `ruff check` — S110 | Silent `except Exception: pass` | Add `as exc` + `logger.debug(...)` + `# noqa: BLE001` |

### Critical Development Rules

```
✅ All async I/O → asyncio. No time.sleep() → use asyncio.sleep()
✅ XML parsing → defusedxml only (not xml.etree)
✅ HTTP to news sites → Chrome 124 User-Agent from _HEADERS
✅ noqa: SXXX → Ruff only | nosec BXXX → Bandit only (not interchangeable)
✅ DB sessions → never shared between coroutines
✅ Agents are stateless → state lives in PostgreSQL/Redis only
✅ health_check() → required on every new agent
✅ No secrets in code → .env only, never committed
```

### Market Hours

| Exchange | Days | Hours | Notes |
|----------|------|-------|-------|
| NYSE/NASDAQ | Mon–Fri | 9:30–16:00 ET | — |
| TASE | Mon–Thu | 10:00–17:25 IL | Pre-open: 9:45–10:00 |
| TASE | Fri | 10:00–15:45 IL | Early close |

### News Fallback Chain

```
[1] Google Search MCP (localhost:8001)
    ↓ (if unavailable)
[2] Google Custom Search API (GOOGLE_API_KEY)
    ↓ (if not configured)
[3] Google News RSS + Yahoo Finance RSS ← free default
```

### Security — What NOT to Do

```
❌ Never commit .env
❌ Never store tokens/passwords in code
❌ Never skip hooks: --no-verify
❌ Never use xml.etree directly (Bandit B314)
❌ Never allow free-form SQL through MCP (whitelist only)
❌ Never run docker compose down -v without a backup
```
