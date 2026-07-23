# 🧠 INTERNAL_CONTROL_PANEL.md — MarketMind-Pro
> Internal developer runbook | Updated: July 2026

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
| **How it works** | 1. Initializes Redis connection. 2. Starts `QuantEngine` (polls watchlist every 60s). 3. Starts `TradingAgent` (autonomous paper trading loop — idle unless `TRADING_ENABLED=true`). 4. Starts `TelegramDispatcher` (Telegram bot + scheduled jobs). 5. Handles SIGTERM/SIGINT for graceful shutdown. |
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

#### `/sectors`

| Field | Details |
|-------|---------|
| **Command** | `/sectors` |
| **Responsibility** | S&P 500 sector rotation dashboard |
| **How it works** | `_fetch_sector_data()` fetches 11 SPDR ETFs (XLK, XLV, XLF, XLE, XLI, XLY, XLP, XLC, XLB, XLRE, XLU) via `yf.Ticker.fast_info` concurrently, caches in Redis (`sectors:daily`, TTL 600s), returns sorted by daily % change. Emoji thresholds: 🔥 >+1.5%, 🟢 ≥0%, 🟡 ≥-0.5%, 🔴 <-0.5%. Also included in `/news` (no ticker), morning preview, and post-close reports. |
| **Access & Flags** | None |
| **Success Check** | 11 sector rows ranked by % change + breadth line (e.g. "Advancing: 7 / Declining: 4") |

---

#### `/setalert [TICKER] [PRICE]`

| Field | Details |
|-------|---------|
| **Command** | `/setalert AAPL 220` |
| **Responsibility** | Set a price alert for a ticker |
| **How it works** | Fetches the current live price, auto-detects direction (`PRICE_ABOVE` if target > current, `PRICE_BELOW` otherwise), and stores the alert in the PostgreSQL `user_alert` table. `_job_check_alerts` evaluates all active alerts every 5 minutes; a triggered alert sends a notification and is deactivated. |
| **Access & Flags** | `TICKER PRICE` — both required |
| **Success Check** | Confirmation message with direction + target; `/myalerts` shows the new alert |

---

#### `/myalerts`

| Field | Details |
|-------|---------|
| **Command** | `/myalerts` |
| **Responsibility** | List the caller's active price alerts |
| **How it works** | Queries `user_alert` for rows with the caller's `chat_id` and `is_active=true` |
| **Access & Flags** | None |
| **Success Check** | List of alerts (ticker, direction, threshold) or "no active alerts" |

---

#### `/cancelalert [TICKER]`

| Field | Details |
|-------|---------|
| **Command** | `/cancelalert AAPL` |
| **Responsibility** | Deactivate the caller's alert(s) for a ticker |
| **How it works** | Sets `is_active=false` on matching `user_alert` rows for the caller's `chat_id` |
| **Access & Flags** | `TICKER` — required |
| **Success Check** | Confirmation message; alert no longer appears in `/myalerts` |

---

#### `/portfolio`

| Field | Details |
|-------|---------|
| **Command** | `/portfolio` |
| **Responsibility** | Live StockArena paper-trading portfolio snapshot — **admin only** |
| **How it works** | Calls the StockArena REST API via `StockArenaClient`: cash, total value, overall return, open positions with unrealized P&L, plus current strategy weights from `StrategyAllocator` and the last per-ticker decisions from Redis (`trading:last_decision:{ticker}`) |
| **Access & Flags** | Restricted to `TELEGRAM_CHAT_ID` |
| **Success Check** | Message with cash, value, return %, positions, strategy weights |

---

#### `/trading on|off|status`

| Field | Details |
|-------|---------|
| **Command** | `/trading off`, `/trading on`, `/trading status` |
| **Responsibility** | Autonomous trading kill-switch + status — **admin only** |
| **How it works** | Writes the Redis flag `trading:enabled` (`on`/`off`). The agent trades only when `TRADING_ENABLED=true` (env) **and** the Redis flag is not `off`. `status` (default with no args) shows the env switch, Redis kill-switch state, extended-hours setting, and StockArena API health. Inline buttons for on/off/status also exist via callbacks. |
| **Access & Flags** | Restricted to `TELEGRAM_CHAT_ID` |
| **Success Check** | `status` reflects the change immediately; agent logs `trading_disabled_by_redis` when off |

---

#### `/force_macro_off on|off|status`

| Field | Details |
|-------|---------|
| **Command** | `/force_macro_off on`, `/force_macro_off off`, `/force_macro_off status` |
| **Responsibility** | Manual macro buy-halt override + current macro-gate state — **admin only** |
| **How it works** | `on` writes Redis `trading:macro:force_off=on`; the Orchestrator then forces `MacroState.decision=OFF` (no new buys, exits still run) until cleared with `off`. `status` shows the manual flag plus the last gate regime/decision/size from `trading:macro:state`. |
| **Access & Flags** | Restricted to `TELEGRAM_CHAT_ID` |
| **Success Check** | Next cycle logs `macro_gate_off_skipping_buys`; `/portfolio` macro line shows `OFF` |

---

#### `/pause_agent <name>` · `/resume_agent <name>`

| Field | Details |
|-------|---------|
| **Command** | `/pause_agent core_momentum`, `/resume_agent core_momentum` |
| **Responsibility** | Freeze/unfreeze one thematic agent — **admin only** |
| **How it works** | `pause` sets `trading:agent:{name}:halted_until` far in the future; `resume` deletes it. A frozen agent's strategies are excluded from new buys (its exits still run); the other agents keep trading. Valid names come from `build_agents()` (core_momentum, low_vol_quality, defensive, …). |
| **Access & Flags** | Restricted to `TELEGRAM_CHAT_ID` |
| **Success Check** | `/portfolio` Agents section shows `⏸️ frozen` for the paused agent |

---

#### `/sync_positions`

| Field | Details |
|-------|---------|
| **Command** | `/sync_positions` |
| **Responsibility** | Force a StockArena reconciliation on the next cycle — **admin only** |
| **How it works** | Sets Redis `trading:force_reconcile=on`; the Orchestrator re-imports server trade history / positions at the start of its next cycle, then clears the flag. |
| **Access & Flags** | Restricted to `TELEGRAM_CHAT_ID` |
| **Success Check** | Orchestrator logs `manual_reconcile_done` on the next cycle |

---

#### `/backtest [TICKERS...]`

| Field | Details |
|-------|---------|
| **Command** | `/backtest` or `/backtest AAPL MSFT SPY` |
| **Responsibility** | Trigger the offline strategy backtest from the phone — **admin only** |
| **How it works** | Runs `src.backtest.runner.run_backtest_async` in a background thread (mode `all`; no args → rotation of the 8 least-tested tickers). Local only: yfinance + parquet cache, simulated broker — nothing is sent to StockArena. Replies when done with the winning strategy + the local Hebrew report path. The "📥 אמץ משקולות" button writes cumulative seeds to `data/backtest_weights.json`. |
| **Access & Flags** | Restricted to `TELEGRAM_CHAT_ID`. One run at a time (in-process lock). |
| **Success Check** | "⏳ …יצאה לדרך" immediately; "✅ הבדיקה הסתיימה" with the report path when finished |

---

#### `/health`

| Field | Details |
|-------|---------|
| **Command** | `/health` |
| **Responsibility** | Full system health dashboard — **admin only** |
| **How it works** | Checks concurrently: 1. PostgreSQL — `SELECT 1`. 2. Redis — `PING`. 3. MCP Search Server — `GET localhost:8001/health`. 4. MCP SQL Server — `GET localhost:8002/health`. 5. Google News RSS reachability. 6. `QuantEngine.health_check()`. 7. Trading bot — StockArena API health (or "disabled" / "token not set"). |
| **Access & Flags** | Restricted to `TELEGRAM_CHAT_ID` only — other authorized users receive `🔒 This command is restricted to the bot admin.` |
| **Success Check** | ✅ for every service. ⚠️ = warning, ❌ = failure |

---

### ⏰ Scheduled Jobs (APScheduler)

| Trigger | Responsibility | How it works | Access & Flags | Success Check |
|---------|---------------|--------------|----------------|---------------|
| **9:00 AM ET, Mon–Fri** | Pre-market report | `_job_market_preview()` — analyzes: AAPL, MSFT, NVDA, SPY, QQQ with RSI + global snapshot | Automatic — no parameters | Telegram message with header "🌅 Pre-Market Preview" |
| **4:15 PM ET, Mon–Fri** | Market close report | `_job_market_close_regular()` — analyzes: AAPL, MSFT, NVDA, GOOGL, SPY with full signals | Automatic — no parameters | Telegram message with header "📊 Market Close Summary" |
| **Every 5 min** | Price alert check | `_job_check_alerts()` — fetches live prices for all active `user_alert` rows; triggered alerts notify the owner and are deactivated | Automatic — no parameters | Alert notification arrives; alert disappears from `/myalerts` |
| **Sunday 18:00 IL** | Weekly strategy report | `_job_weekly_strategy_report()` — per-strategy weight, avg return, win rate, and signal count from `StrategyAllocator`; lists disabled (weight-0) strategies; portfolio return vs SPY-this-week line. Skipped when `TRADING_ENABLED=false` | Automatic — no parameters | Telegram message with header "🧠 WEEKLY STRATEGY REPORT" |

---

### 🧪 Backtesting Environment (offline, local, free)

#### `bash scripts/backtest.sh [start|status|stop]`

| Field | Details |
|-------|---------|
| **Command** | `bash scripts/backtest.sh` (= `start`), `status`, `stop` |
| **Where it runs** | **Locally on your Mac**, as one Python process — no Docker, no Redis/Postgres, no cloud, and completely separate from the live bot. Ends by itself; `stop` kills it mid-run. |
| **Responsibility** | Test all 21 strategies (the live 8 promoted winners + 13 experimental backtest-only) plus FOUR agent simulations — `full_agent` (all 18), `full_agent_live` (the promoted 8 = the real live configuration), `walk_forward`, `walk_forward_live` — on decades of history across a ~435-ticker pool, accumulate learning across runs, and produce weight seeds for the live allocator |
| **How it works** | `start` runs `python -m src.backtest --mode all --tickers ALL --export-weights` in the background (PID in `data/backtest.pid`, log in `reports/backtest.log`) and **opens the Hebrew report in the browser** when done. Standalone strategy runs execute in parallel (fork process pool). Data: yfinance daily bars cached in `data/cache/backtest/` (free, no API key). Execution: simulated broker with $2.50/order commission + 0.05% slippage; fills at next-day open. Validation: walk-forward (3y train → 1y unseen test). |
| **Outputs** | `reports/backtest_<date>.html` (Hebrew, layman-friendly, incl. the "מה הסוכן למד" cumulative verdict table), `reports/index.html` (all runs + cumulative ranking), `data/backtest_history.json` (learning memory), `data/backtest_weights.json` (live-agent seeds, regime-aware, clamped ±20% on consumption) |
| **Access & Flags** | `python -m src.backtest --mode {strategies,agent,walkforward,sweep,all,stress,montecarlo} --tickers ... | --rotate N --start --end --cash --slippage-bps --export-weights --macro-gate --no-cache`. `--macro-gate` adds a `full_agent_live_gated` A/B variant; `--mode stress` = 2008/2020/2022 crisis windows (gated vs ungated, 3× slippage); `--mode montecarlo` = bootstrap the trade sequence into a CAGR/maxDD distribution (overfitting check). |
| **Success Check** | `status` shows RUNNING then the report path; SPY buy&hold CAGR over 30y lands ≈ 8–11%; `ps` clean after finish |

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
| `pytest tests/unit/ -v` | Run all 351 unit tests | No I/O — does not require Docker | `-v` verbose. `-x` stop on first failure | `351 passed` |
| `pytest tests/unit/ --cov=src/quant --cov-report=term-missing` | Tests + coverage report | Computes coverage on `src/quant/` — must be ≥80% | `--cov-report=html` — HTML report in `htmlcov/` | `TOTAL ... 80%+` |
| `pytest tests/integration/ -m integration -v` | Integration tests (requires Docker) | Connects to PostgreSQL, creates tables, performs CRUD | Requires: `docker compose up -d postgres redis` | `4 passed` |
| `pytest tests/unit/test_indicators.py -v` | Run a specific test file | Runs only indicator tests | — | `X passed` |
| `pytest tests/unit/test_indicators.py::test_rsi_known_values -v` | Run a single test | Runs one test by name | — | `1 passed` |
| `pytest tests/unit/ -m "not slow"` | All tests except slow ones | Filters by marker | — | — |

**Test files:**
```
tests/unit/
├── test_indicators.py             — RSI, MACD, SMA, EMA, volume spike, momentum score
├── test_fibonacci.py              — Fibonacci levels, support/resistance
├── test_arbitrage.py              — TASE/NYSE gap calculation
├── test_fundamentals.py           — EarningsReport, _fmt_revenue, format_earnings_english
├── test_timezone_utils.py         — market hours, timezone helpers
├── test_strategies.py             — 4 trading strategies (fail-closed HOLD behavior)
├── test_allocator.py              — StrategyAllocator weights + signal scoring
├── test_risk.py                   — RiskManager exits, sizing, circuit breaker, commissions
├── test_universe.py               — UniverseScanner filters + fallback
├── test_stockarena_client.py      — envelope unwrap, retries, fatal errors, trade timeout
└── test_backtest_*.py             — backtest framework (features, broker, risk_sim,
                                     allocator_sim, universe_sim, walkforward, regimes,
                                     metrics, history)

tests/integration/
└── test_database.py               — PostgreSQL models (PriceHistory, UserAlert, DualListingGap)
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

### Version 1.8.0 (July 2026)

| Feature | Description |
|---------|-------------|
| **Data-Quality Gate** | A real audit of the 431-ticker cache found corrupt segments feeding every backtest (HUBB: 1,861-bar frozen run 1977–84 + an 886% artifact jump). `validate_history` (in `load_history`) strips corrupt LEADING segments — frozen runs >60 bars and >250% single-bar jumps — keeping genuine history and real crashes (JAZZ +210% in 2009 preserved). Re-runnable: `python -m src.backtest.validate` |
| **Survivorship Honesty** | The pool is 100% current survivors, so all returns are upward-biased. The Hebrew report now leads with a prominent caveat banner + a summary of exactly what the gate cleaned |
| **3 ATR Strategies** | `Supertrend` (ATR trend, 10.8% CAGR standalone in smoke), `AdxTrend` (Wilder ADX>25 + DI), `KeltnerBreakout` (EMA±ATR channel). New indicators: `atr`, `adx`, `supertrend`; ATR-family signal fields need High/Low (`generate_signals(..., high, low)`) |
| **21 Strategies Total** | 8 live + 13 experimental; feature-equivalence verified (vectorized ≡ per-bar slice, incl. iterative Supertrend at 0.0 error) |
| **351 Unit Tests** | ATR/ADX/Supertrend, the 3 strategies, and the data-quality gate (corrupt → trimmed, clean/real-crash → kept) |

---

### Version 1.7.1 (July 2026)

| Feature | Description |
|---------|-------------|
| **Horizon-Scaled Min Holding** | Attribution showed ts_momentum (best standalone) losing the most inside the agent — its month-long positions were dumped after the flat 72h floor. Minimum holding now = `max(MIN_HOLDING_HOURS, opening strategy's horizon)` (`RiskManager.min_holding_hours_for`, mirrored in the backtest engine) |
| **Smoke Verdict** | 5-ticker 2015→2026: `full_agent_live` **+443% (17.5% CAGR, Sharpe 1.17) — beats SPY buy&hold** (+330%, 14.9%, 0.87) at half the drawdown; `walk_forward_live` +179% (12.8% CAGR) |
| **Live Deployment Off** | `.env`: `TRADING_ENABLED=false` (user request) + `TRADING_DYNAMIC_UNIVERSE=false` (backtests: screener universe −66% vs static +18%) |
| **320 Unit Tests** | Horizon-holding gates (live + sim); universe tests decoupled from the developer's `.env` |

---

### Version 1.7.0 (July 2026)

| Feature | Description |
|---------|-------------|
| **Universe Quality Fix** | Backtests proved the screener universe was why the combined agent lost while every strategy won standalone. `day_losers` removed (negative selection); an uptrend quality gate (price > 200-day average) filters every candidate; `UNIVERSE_QUALITY_RATIO` (0.5) reserves half the universe for the stable watchlist |
| **Drawdown Brake** | Below `DRAWDOWN_BRAKE_PCT` (15%) from the portfolio's all-time high, new-buy budgets are halved (`DRAWDOWN_SIZE_FACTOR`) until recovery — stops the death spiral. HWM in Redis `trading:portfolio_hwm` |
| **Let Winners Run** | Positions opened by ≥480h-horizon strategies (ts_momentum, high_52w, low_vol_trend, golden_cross) skip the +10% take-profit cap and ride the trailing stop; entry strategy recorded in Redis `trading:entry_strategy:{ticker}` |
| **Universe A/B in Backtest** | `--agent-universe {dynamic,full}` + a `full_agent_live_full` simulation quantify what the screener universe costs/earns |
| **Attribution Reporting** | Hebrew report now shows realized P&L per opening strategy inside each agent sim + agent returns per decade |
| **317 Unit Tests** | Universe quality gate/core, drawdown brake, horizon-aware exits, P&L attribution |
| **Smoke Verdict** | 5-ticker 2015→2026: `full_agent_live` +172% (10.0% CAGR, Sharpe 0.78, maxDD −16.5%) vs +6.3% before this round |

---

### Version 1.6.0 (July 2026)

| Feature | Description |
|---------|-------------|
| **Strategy Library ×18** | 14 new strategies added over two rounds: RSI-2 & Double-7 (Connors), Bollinger reversion, Donchian/Turtle breakout, 12−1-month time-series momentum, 52-week-high momentum, dip buyer, gap momentum, multi-timeframe, MACD cross, SMA50 pullback, golden cross events, volatility-contraction breakout, low-volatility trend |
| **Live Set Promoted to 8** | Promotion based on the 64-year full-pool backtest: TrendFollowing, Breakout, DipBuyer, GapMomentum, MultiTimeframe, BollingerReversion, TSMomentum (11.5% CAGR winner), FiftyTwoWeekHigh. MomentumDaily + MeanReversion demoted to experimental |
| **Evidence-Sharpened Learning** | `compute_weights`: `1 + WEIGHT_SENSITIVITY(50)×avg`, cap 3.0; **negative average ⇒ strategy disabled (weight 0)** with automatic re-enable on recovery; zero-weight signals filtered in both live agent and backtest |
| **Anti-Churn Gates** | `MIN_HOLDING_HOURS` (72) + `SELL_CONFIDENCE_GATE` (0.6) — strategy SELLs can't dump another strategy's fresh entry; protective exits exempt |
| **Smarter Execution** | Sells execute before buys; buys ranked by `confidence × weight` across all tickers; volatility-aware sizing (`TARGET_POSITION_VOL`) shrinks positions in wild names |
| **Live-Config Simulations** | Backtest now also runs `full_agent_live` + `walk_forward_live` (the promoted 8 only) — the honest predictor of real bot behavior |
| **Candidate Pool ×435** | `--tickers ALL` (now the `backtest.sh` default) runs the full ~435-name pool; parallel strategy runs via fork process pool |
| **308 Unit Tests** | Learning math, anti-churn gates, regime-conditional weights, vol sizing, ranked execution, feature-equivalence for all new indicator fields |

---

### Version 1.5.0 (July 2026)

| Feature | Description |
|---------|-------------|
| **Autonomous Trading Agent** | `TradingAgent` paper-trades a simulated $10k StockArena account: 4 strategies × 3 timeframes, dynamic universe scan via Yahoo screeners, performance-weighted allocation, full risk management (stop-loss, trailing stop, take-profit, circuit breaker, commissions). Off by default (`TRADING_ENABLED=false`) + Redis kill-switch |
| **`/portfolio` + `/trading`** | Admin commands: live portfolio snapshot and trading kill-switch/status |
| **Backtesting Framework** | `src/backtest/` — offline walk-forward backtests over ~30y of daily bars (`bash scripts/backtest.sh`, `/backtest` in Telegram). Hebrew report + cumulative history; `--export-weights` seeds the live allocator (`data/backtest_weights.json`, regime-aware) |
| **Price Alerts** | `/setalert TICKER PRICE` (auto above/below), `/myalerts`, `/cancelalert`; checked every 5 min by `_job_check_alerts` |
| **Weekly Strategy Report** | Sunday 18:00 IL — per-strategy weight, avg return, win rate (when trading is enabled) |
| **210 Unit Tests** | Added trading (strategies, allocator, risk, universe, StockArena client) + backtest suites |
| **New DB Tables** | `trade_records`, `strategy_signals`, `strategy_performance` (migration `0002_trading_tables`) |

---

### Version 1.4.0 (April 2026)

| Feature | Description |
|---------|-------------|
| **Bot Authorization** | `ALLOWED_USER_IDS` env var whitelist — unauthorized users get `🔒 Access Denied` + their user ID. Admin receives `⚠️ Unauthorized Access Attempt` alert with name, user ID, username, attempted command, and timestamp |
| **Open mode** | Leave `ALLOWED_USER_IDS` empty → all users can access (useful to discover your own ID before lockdown) |
| **`/health` admin-only** | Even authorized users cannot run `/health` — only `TELEGRAM_CHAT_ID` (the admin) can see system internals |
| **`_parse_user_ids` validator** | Handles empty string, single int, and comma-separated string → `list[int]`. `env_ignore_empty=True` in Settings prevents pydantic parse errors on empty env vars |

---

### Version 1.3.0 (April 2026)

| Feature | Description |
|---------|-------------|
| **Live Prices** | All price displays use `yf.Ticker.fast_info.last_price` — reflects pre-market, regular, and after-hours in real time |
| **Session Labels** | 🌅 pre-market / 🌙 after-hours labels on price lines in `/analyze`, snapshot header, and sector block headers |
| **`/sectors`** | New command: 11 SPDR sector ETFs ranked by daily % change. Cached 10 min. Included in `/news`, morning preview, and post-close reports |
| **Earnings** | `fetch_earnings_report()` — EPS/revenue actual vs estimate, beat/miss, YoY growth. Shown in `/analyze` |
| **Momentum Score** | `momentum_score()` — composite signal from RSI + MACD + MA alignment shown in `/analyze` |
| **84 Unit Tests** | Added `test_fundamentals.py` (29 tests) covering EarningsReport and formatting |

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
| `trade_records` | ticker, action (BUY/SELL), quantity, fill_price, notional, strategy, reason, portfolio_value_after, cash_after, executed_at | Every executed paper trade (audit trail) |
| `strategy_signals` | strategy, ticker, action, confidence, decision_price, eval_after, scored, virtual_return_pct | Every non-HOLD strategy signal, scored after its horizon |
| `strategy_performance` | strategy (unique), signals_scored, avg_return_pct, win_rate | Rolling per-strategy stats driving capital weights |

### Redis Cache Keys

| Key | TTL | Source |
|-----|-----|--------|
| `quote:{ticker}:{timeframe}` | 60s (default) | QuantEngine |
| `sentiment:{ticker}` | 900s (15 min) | NewsSearchAgent |
| `fundamentals:{ticker}` | 14400s (4 hours) | fundamentals.py |
| `sectors:daily` | 600s (10 min) | `_fetch_sector_data()` in telegram_dispatcher |
| `trading:enabled` | none | `/trading on\|off` kill-switch |
| `trading:weights:{regime}` | 3600s | StrategyAllocator (per BULL/BEAR/VOLATILE regime) |
| `trading:cooldown:{ticker}` | 30 min | RiskManager per-ticker cooldown |
| `trading:hwm:{ticker}` | 30d | RiskManager trailing-stop high-water mark |
| `trading:entry_time:{ticker}` | 30d | RiskManager minimum-holding gate (anti-churn) |
| `trading:entry_strategy:{ticker}` | 30d | RiskManager horizon-aware profit exits |
| `trading:portfolio_hwm` | none | RiskManager drawdown brake (all-time portfolio high) |
| `trading:day_open_value:{day}` / `trading:halted_until:{day}` | 1–2d | Daily circuit breaker |
| `trading:last_decision:{ticker}` | 3600s | TradingAgent audit trail (shown in `/portfolio`) |
| `trading:universe` | 900s (15 min) | UniverseScanner dynamic candidate list |

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
✅ Trading is double-gated → TRADING_ENABLED (env) AND Redis trading:enabled != "off"
✅ Fatal StockArena errors (UNAUTHORIZED/FORBIDDEN/BOT_INACTIVE) → stop loop, never retry
✅ Trade-POST timeout → reconcile against server history, never blind-retry
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
