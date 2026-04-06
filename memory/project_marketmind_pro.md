---
name: MarketMind-Pro Project Architecture
description: Core architecture, tech stack, agent contracts, implemented features, and key design decisions for MarketMind-Pro
type: project
---

MarketMind-Pro is a professional-grade autonomous trading intelligence system for TASE & US markets, optimized for Apple Silicon M4 (arm64).

**Why:** Built as a full production-grade system — not a prototype. Designed for real-money market intelligence with TASE dual-listing arbitrage as a core differentiator.

**How to apply:** All future work should respect the agent-first, stateless architecture. Agents read from DB/Redis, never hold state in memory.

---

## Key Design Decisions
- Python 3.12+ (arm64 native, not Rosetta)
- Async-first: asyncio everywhere, SQLAlchemy async engine, aioredis
- 3 autonomous agents: News-Search-Agent, Quant-Engine, Telegram-Dispatcher
- 2 MCP servers: Google Search (port 8001), SQL (port 8002)
- PostgreSQL via SQLAlchemy 2.0 + Alembic migrations (5 tables)
- Redis for 60s quote cache + 15min news sentiment cache + 4h fundamentals cache
- Docker: multi-stage arm64, postgres:16-alpine + redis:7-alpine
- All Telegram output: Hebrew (ParseMode.HTML — NOT Markdown)

---

## Trading Schedule (as of v1.1.0 — corrected)
- **TASE (ת"א):** Monday–Friday
  - Mon–Thu: 10:00–17:25 Israel time (pre-open 09:45)
  - Friday: 10:00–15:45 Israel time (early close)
  - Saturday/Sunday: closed
- **NYSE:** Monday–Friday 09:30–16:00 ET (unchanged)

---

## Automated Scheduled Reports (APScheduler via python-telegram-bot JobQueue)
- **09:30 IL (Mon–Fri):** Hebrew pre-open preview + RSI snapshot of TEVA/NICE/CHKP
- **17:45 IL (Mon–Thu):** End-of-day Hebrew summary
- **16:05 IL (Friday):** Friday early-close Hebrew summary

---

## Quant Coverage
- SMA/EMA: 20, 50, 100, 150, 200 periods
- RSI (Wilder's smoothing, 14-period), MACD (12/26/9)
- 10-day volume spike detection (2x multiplier)
- Fibonacci: 52W H/L retracements (23.6%–78.6%) + extensions (127.2%, 161.8%, 261.8%)
- USD/ILS arbitrage for: TEVA, NICE, CHKP, AMDOCS, CEVA, GILT, RADCOM, TOWER, ORCL

---

## Fundamentals Module (src/quant/fundamentals.py — added v1.1.0)
Data source: yfinance Ticker.info + Ticker.insider_transactions
- CompanyProfile: name, sector, industry, summary, market_cap, pe_trailing, pe_forward, eps_trailing, eps_forward, dividend_yield, target_price_mean, week_52_high/low, currency
- InsiderTx: insider_name, title, date, BUY/SELL, shares, price_per_share, total_value, filing_url
- Competitor static map (COMPETITOR_MAP): 9 dual-listed + major US stocks
- Redis cache TTL: 14400s (4 hours)
- DB persistence: InsiderTransaction ORM model (table already existed)
- Hebrew formatters: format_profile_hebrew(), format_insiders_hebrew()

---

## GitHub Pages Publisher (src/ui/publisher.py — added v1.1.0)
- Uses GitHub Contents API (httpx PUT) — no gitpython dependency
- Publishes to: docs/{ticker}_chart.html in github_pages_repo
- Public URL: https://maaoor6.github.io/MarketMind-Pro/{ticker}_chart.html
- Requires: GITHUB_TOKEN + GITHUB_PAGES_REPO in .env
- Best-effort: publish failures do not block /analyze response

---

## Telegram Bot Commands (all Hebrew, ParseMode.HTML)
- /start — תפריט ראשי + מצב שוק
- /analyze [TICKER] — ניתוח מלא: מחיר, RSI, MACD, ממוצעים נעים, פיבונאצ'י, פונדמנטלי, עסקאות בעלי עניין, ארביטראז', סנטימנט, לינק גרף
- /fibonacci [TICKER] — רמות פיבונאצ'י מלאות
- /arbitrage [TICKER] — פער TASE/NYSE
- /compare [TICKER1] [TICKER2] — השוואה צד-לצד (חדש v1.1.0)
- /health — סטטוס מערכת

---

## Local Dashboard (src/ui/dashboard.py — Streamlit)
- Run: `streamlit run src/ui/dashboard.py` or via Docker on port 8501
- URL: http://localhost:8501
- Shows: candlestick + volume + RSI, Fibonacci table, arbitrage, key metrics

---

## Currency Detection
- currency_symbol(ticker) in src/utils/timezone_utils.py
- Returns '₪' if ticker ends in '.TA', else '$'
- Used everywhere prices are displayed in Telegram and Dashboard

---

## run_loop (QuantEngine — active as of v1.1.0)
- Polls every 60s during market hours
- Watchlist: DUAL_LISTED.keys() + ["AAPL", "MSFT", "SPY", "QQQ"] (deduplicated)
- Each tick: analyze() → cache in Redis "signal:{ticker}" (TTL=120) → upsert PriceHistory ORM

---

## File Map
```
src/
  agents/
    news_search_agent.py     — Google MCP + keyword sentiment
    quant_engine.py          — QuantEngine + run_loop + OHLCV persistence
    telegram_dispatcher.py   — Bot, all commands, scheduled jobs
  quant/
    indicators.py            — SMA, EMA, RSI, MACD, volume spike
    fibonacci.py             — 52W retracements + extensions
    arbitrage.py             — USD/ILS dual-listing gap
    fundamentals.py          — P/E, EPS, insiders, competitors [NEW v1.1.0]
  database/
    models.py                — 5 ORM tables (PriceHistory, DualListingGap, UserAlert, InsiderTransaction, SentimentRecord)
    session.py               — AsyncSessionLocal
    cache.py                 — RedisCache wrapper
  mcp/
    google_search_mcp.py     — Google Search MCP server (port 8001)
    sql_mcp_server.py        — SQL MCP server (port 8002)
  ui/
    charts.py                — Plotly dark-mode candlestick
    dashboard.py             — Streamlit local dashboard
    publisher.py             — GitHub Pages publisher [NEW v1.1.0]
  utils/
    config.py                — pydantic-settings
    logger.py                — structlog
    timezone_utils.py        — Market hours, currency_symbol [UPDATED v1.1.0]
```

---

## GitHub
- Repo: https://github.com/maaoor6/MarketMind-Pro.git
- Branch: main
- CI: GitHub Actions (.github/workflows/ci.yml) — ruff, black, bandit, pytest

## Docker Services
- marketmind-postgres: port 5432
- marketmind-redis: port 6379
- marketmind-app: main bot
- marketmind-dashboard: Streamlit on port 8501
- marketmind-mcp-search: port 8001
- marketmind-mcp-sql: port 8002

## Test Coverage
- 44 unit tests, all passing (updated timezone test: Sunday closed, Monday open)
- Integration tests require docker-compose services
