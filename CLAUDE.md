# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview
MarketMind-Pro is an autonomous trading intelligence system for US markets and TASE (Tel Aviv Stock Exchange). It runs as a Telegram bot delivering technical analysis, news sentiment, market snapshots, and interactive Plotly charts published to GitHub Pages.

---

## Common Commands

```bash
# Start all services (PostgreSQL, Redis, app, dashboard, MCP servers)
docker compose up -d --build

# Rebuild after code changes
docker compose down && docker compose up -d --build

# Run unit tests (no Docker needed)
pytest tests/unit/ -v

# Run a single test file
pytest tests/unit/test_indicators.py -v

# Run a single test by name
pytest tests/unit/test_indicators.py::test_rsi_known_values -v

# Run integration tests (requires Docker services running)
pytest tests/integration/ -v -m integration

# Lint / format / security
ruff check src/ tests/
black src/ tests/
bandit -r src/ -ll

# Database migrations
alembic upgrade head
alembic revision --autogenerate -m "description"

# Streamlit dashboard (local, no Docker)
streamlit run src/ui/dashboard.py

# Install git pre-commit hooks
bash scripts/install_hooks.sh
```

---

## Architecture

### Data Flow
```
Telegram command or scheduled job
  → telegram_dispatcher.py (handler / job)
    → quant_engine.py          — price data fetch + technical signals
    → news_search_agent.py     — sentiment via Google News RSS
    → fundamentals.py          — company profile + insider transactions
    → publisher.py             — Plotly chart → GitHub Pages
  → HTML message + InlineKeyboard → user
```

### Telegram Bot Commands
All handlers are in `src/agents/telegram_dispatcher.py`:

| Command | Handler | Description |
|---------|---------|-------------|
| `/start` | `cmd_start` | Welcome message with NYSE status + countdown, inline menu |
| `/analyze [TICKER]` | `cmd_analyze` | Full report: price, signals, RSI, MACD, Fibonacci, fundamentals, insider trades, news sentiment, chart button |
| `/news [TICKER]` | `cmd_news` | Top 5 live headlines with snippets; shows global market snapshot if no ticker given |
| `/fibonacci [TICKER]` | `cmd_fibonacci` | 52-week Fibonacci retracement + extension levels with support/resistance |
| `/compare T1 T2` | `cmd_compare` | Side-by-side comparison table (price, RSI, MACD, Fibonacci, sentiment) |
| `/health` | `cmd_health` | System dashboard: DB, Redis, MCP, news RSS connectivity, quant engine status |
| _(any text)_ | `cmd_fallback` | Unrecognized messages → quick-action inline keyboard |

**Inline keyboard callbacks** (`callback_handler`): `analyze:TICKER`, `fib:TICKER`, `news:TICKER`, `health`, `market_open`, `prompt_analyze`, `prompt_fibonacci`, `prompt_compare`.

### Scheduled Jobs (APScheduler via python-telegram-bot JobQueue)
Both jobs run **Mon–Fri only** in US Eastern Time:
- **9:00 AM ET** — `_job_market_preview`: pre-market snapshot for watchlist `[AAPL, MSFT, NVDA, GOOGL, SPY]`
- **4:15 PM ET** — `_job_market_close_regular`: post-close summary for same watchlist

### QuantEngine (`src/agents/quant_engine.py`)
- `analyze(ticker) → QuantSignal` — full technical analysis (RSI, MACD, Fibonacci, volume spike, market status)
- `fetch_price_data(ticker, period, interval) → pd.DataFrame` — yfinance with Redis caching
- `run_loop()` — continuous polling loop (every 60s during market hours)
- `_poll_watchlist()` — parallel analysis of watchlist: `AAPL, MSFT, NVDA, GOOGL, META, SPY, QQQ, TSLA`
- `_upsert_price_history()` — persists OHLCV to PostgreSQL
- Cache key: `quote:{ticker}:{timeframe}` — TTL: `settings.quote_cache_ttl` (default 60s for 1m; longer for daily)

### Indicators (`src/quant/indicators.py`)
All return typed dataclasses or Series:
- `rsi(prices, period=14) → RSIResult` — Wilder's smoothing method
- `macd(prices, fast=12, slow=26, signal=9) → MACDResult`
- `sma(prices, period) → pd.Series`
- `ema(prices, period) → pd.Series`
- `volume_spike(volume, ma_period=10, spike_multiplier=2.0) → bool` — volume > 2× 10-day MA
- `all_moving_averages(prices) → dict` — SMA + EMA for periods 20, 50, 100, 150, 200
- `generate_signals(prices, volume) → dict` — comprehensive signal dict (used for caching)

### Fibonacci (`src/quant/fibonacci.py`)
- `calculate_fibonacci(prices, ticker, window_days=252) → FibonacciLevels` — computed from 52-week H/L
- Retracement levels: 0%, 23.6%, 38.2%, 50%, 61.8%, 78.6%, 100%
- Extension levels: 127.2%, 161.8%, 261.8%
- Trend: compares last 20 days vs earlier prices
- `format_fibonacci_message(levels) → str` — Telegram HTML output

### Arbitrage (`src/quant/arbitrage.py`)
- `calculate_arbitrage(ticker_us, price_us_usd, price_tase_ils, usd_ils_rate) → ArbitrageSignal`
- `get_usd_ils_rate() → float` — ExchangeRate-API with 3.72 hardcoded fallback
- Min gap threshold: 0.5%
- Dual-listed stock map: `TEVA→TEVA.TA`, `NICE→NICE.TA`, `CHKP→CHKP.TA`, `AMDOCS→DOX.TA`, `CEVA→CEVA.TA`, `GILT→GILT.TA`, `RADCOM→RDCM.TA`, `TOWER→TSEM.TA`, `ORCL→ORCL.TA`

### Fundamentals (`src/quant/fundamentals.py`)
- `fetch_company_profile(ticker) → CompanyProfile` — yfinance, cached 4h in Redis (`fundamentals:{ticker}`)
- `fetch_insider_transactions(ticker) → list[InsiderTx]` — up to 10 most recent, newest first
- `save_insider_transactions(ticker, txns) → int` — upserts to PostgreSQL, deduplicates by `(ticker, insider_name, transaction_date, shares)`
- `get_competitors(ticker) → list[str]` — static map for TEVA, AAPL, MSFT, NVDA, GOOGL, etc.
- `format_profile_english(profile) → str` — HTML for Telegram
- `format_insiders_english(ticker, txns) → str` — HTML for Telegram

### News Search Agent (`src/agents/news_search_agent.py`)
`NewsSearchAgent.analyze_sentiment(ticker) → SentimentReport`

Fallback chain (in order):
1. Google Search MCP at `localhost:8001` (optional)
2. Google Custom Search API (requires `GOOGLE_API_KEY` + `GOOGLE_SEARCH_ENGINE_ID`)
3. **Google News RSS + Yahoo Finance RSS** — free, always available, the default path

Key behaviors:
- RSS fetch **requires** `follow_redirects=True` — Google News returns HTTP 302
- Deduplication: max 2 articles per source domain
- Freshness filter: articles ≤ 48h old (falls back to all if nothing fresh)
- Top 5 headlines: source-diverse (max 1 per source)
- `SentimentReport.score`: -1.0 to +1.0; emoji 🟢 ≥0.3, 🔴 ≤-0.3, 🟡 otherwise
- Cache: `sentiment:{ticker}` — TTL: `settings.news_cache_ttl` (default 900s / 15 min)
- Empty results are **not cached** — always retry

### Market Snapshot (`telegram_dispatcher.py → _SNAPSHOT_SYMBOLS`)
ETF-based (not raw futures), grouped for display:
- **Equities**: SPY, VOO, QQQ, DIA, IWM, RSP
- **Currency/Vol**: DX-Y.NYB (DXY), ^VIX
- **Fixed Income**: TLT, AGG
- **Commodities**: GLD, SLV, USO
- **Crypto**: BTC-USD, ETH-USD

Groups (`_EQUITY_ETFS`, `_CURRENCY_VOL`, `_FIXED_INCOME`, `_COMMODITY`, `_CRYPTO`) drive blank-line separators in output.

### Chart Publishing (`src/ui/publisher.py`)
1. `publish_ticker_chart(ticker, df, fib_levels) → str` — generates Plotly HTML + uploads to GitHub Pages
2. `publish_chart(ticker, html_content) → str` — GitHub Contents API PUT to `docs/{ticker}_chart.html`
3. After push, `_wait_for_pages(url, timeout=60)` polls HEAD every 4s until HTTP 200 — avoids user seeing 404
4. Public URL pattern: `https://maaoor6.github.io/MarketMind-Pro/{ticker}_chart.html`

### Market Status (`src/utils/timezone_utils.py`)
- `market_status() → dict` — keys: `nyse_open`, `tase_open`, `tase_preopen`, `tase_friday`, `us_time`, `tase_time`
- NYSE: 9:30 AM – 4:00 PM ET, Mon–Fri
- TASE: 10:00 AM – 5:25 PM IL (Mon–Thu), 10:00 AM – 3:45 PM IL (Fri)
- TASE pre-open: 9:45 AM – 10:00 AM IL
- Helpers: `now_utc()`, `now_us()`, `now_tase()`, `time_to_nyse_open()`, `to_us_time()`, `to_tase_time()`, `currency_symbol(ticker)`

### Database Models (`src/database/models.py`)
| Model | Key Fields |
|-------|-----------|
| `PriceHistory` | ticker, exchange, timestamp, timeframe, open/high/low/close, volume |
| `DualListingGap` | ticker_us, ticker_tase, timestamp, price_us_usd, price_tase_ils, usd_ils_rate, gap_pct, gap_direction |
| `UserAlert` | chat_id, ticker, alert_type (`PRICE_ABOVE/BELOW`, `RSI_OVERSOLD`, `VOLUME_SPIKE`, `FIBONACCI`), threshold, is_active |
| `InsiderTransaction` | ticker, insider_name, insider_title, transaction_date, transaction_type, shares, price_per_share, total_value, filing_url |
| `SentimentRecord` | ticker, timestamp, score, headline_count, sources (JSON), summary_en |

### Redis Cache Keys
| Key | TTL | Source |
|-----|-----|--------|
| `quote:{ticker}:{timeframe}` | `settings.quote_cache_ttl` (60s default) | QuantEngine |
| `sentiment:{ticker}` | `settings.news_cache_ttl` (900s default) | NewsSearchAgent |
| `fundamentals:{ticker}` | 14400s (4h) | fundamentals.py |

### MCP Servers (both optional — app degrades gracefully)
**Google Search MCP** (`localhost:8001`, `src/mcp/google_search_mcp.py`):
- `POST /tools/search_web` — `{query, num_results}`
- `POST /tools/scrape_page` — `{url}`
- `POST /tools/search_financial_news` — `{ticker, language}`
- Whitelisted sites: globes.co.il, bizportal.co.il, cnbc.com, reuters.com, bloomberg.com, marketwatch.com

**SQL MCP** (`localhost:8002`, `src/mcp/sql_mcp_server.py`):
- `POST /tools/query_prices` — `{ticker, from_date, to_date, limit}`
- `POST /tools/get_arbitrage_history` — `{ticker_us, min_gap_pct, limit}`
- `POST /tools/get_alerts` — `{chat_id, ticker}`
- `POST /tools/get_sentiment_history` — `{ticker, limit}`
- `POST /tools/get_volume_spikes` — `{ticker, limit}`

### Streamlit Dashboard (`src/ui/dashboard.py`)
Local UI at `http://localhost:8501`. Sidebar: ticker, period (3mo–5y), MA selection, Fibonacci toggle. Main area: key metrics row, RSI/MACD/volume-spike signals, 3-panel dark-mode chart (candlestick+MAs, volume, RSI), Fibonacci retracement + extension tables.

---

## Coding Standards

- **Formatter**: Black (line-length = 88)
- **Linter**: Ruff with isort. Use `# noqa: XXXX` for Ruff, `# nosec BXXX` for Bandit — they are separate systems
- **Type hints**: Required on all public functions and class attributes
- **Docstrings**: Google-style
- **XML parsing**: Use `defusedxml` (`from defusedxml.ElementTree import fromstring as safe_fromstring`) — Bandit B314 blocks commits if `xml.etree` is used directly. Ruff N81x rejects CamelCase aliases.
- **HTTP headers**: All outbound requests to financial news sites must use the Chrome 124 User-Agent from `_HEADERS` in `news_search_agent.py` to avoid 403/429

### Architecture Rules
1. Agents are **stateless** — state lives in PostgreSQL or Redis, never in memory.
2. All async I/O uses `asyncio`. No `time.sleep()` — use `asyncio.sleep()`.
3. Database sessions are **never** shared between threads/coroutines.
4. `async_engine` in `session.py` is a module-level singleton bound to the first event loop. Integration tests must create their own engine per test — see `tests/integration/test_database.py`.
5. Every agent has a `health_check()` method returning `{"status": "ok"|"error", "detail": str}`.

---

## Pre-Commit Hook
Located in `.git/hooks/pre-commit` (install via `scripts/install_hooks.sh`). Runs in order:
1. `ruff check src/ tests/` — fail on any lint error
2. `black --check src/ tests/` — fail if formatting needed
3. `bandit -r src/ -ll` — fail on MEDIUM+ severity findings
4. `pytest tests/unit/ -x -q` — fail if any unit test fails

---

## Environment Variables
All defined in `.env.example`. See `src/utils/config.py` (`Settings` class) for defaults.

**Required for core operation:**
- `DATABASE_URL` — async: `postgresql+asyncpg://...`
- `DATABASE_URL_SYNC` — sync (Alembic): `postgresql+psycopg2://...`
- `REDIS_URL`
- `TELEGRAM_TOKEN` — from @BotFather
- `TELEGRAM_CHAT_ID`

**Required for chart publishing:**
- `GITHUB_TOKEN` — PAT with `repo` scope
- `GITHUB_PAGES_REPO` — `owner/repo` format (default: `maaoor6/MarketMind-Pro`)

**Optional (news tier 2):**
- `GOOGLE_API_KEY` + `GOOGLE_SEARCH_ENGINE_ID` — Google Custom Search API

**Optional (features):**
- `EXCHANGERATE_API_KEY` — live USD/ILS rate (falls back to 3.72 if absent)
- `ALPHA_VANTAGE_KEY` — backup data source
- `QUOTE_CACHE_TTL` — seconds, default 60
- `NEWS_CACHE_TTL` — seconds, default 900

---

## Testing
- `asyncio_mode = "strict"` is set in `pyproject.toml` — all async tests require `@pytest.mark.asyncio`
- `asyncio_default_fixture_loop_scope = "function"` — each test gets its own event loop
- Integration tests require running Docker services: `docker compose up -d postgres redis`
- Minimum 80% coverage target on `src/quant/`
- Mark slow tests with `@pytest.mark.slow`
