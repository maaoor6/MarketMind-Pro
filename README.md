# MarketMind-Pro 📈

> Autonomous trading intelligence for US markets and TASE (Tel Aviv Stock Exchange) — real-time quantitative analysis, fundamentals, news sentiment, and interactive charts via Telegram.

[![Python](https://img.shields.io/badge/Python-3.13+-blue?logo=python)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Apple%20Silicon%20M4-black?logo=apple)](https://apple.com)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Version](https://img.shields.io/badge/Version-1.1.0-orange)]()

---

## What It Does

| Capability | Description |
|---|---|
| **Technical Analysis** | RSI, MACD, SMA/EMA (20–200), volume spike detection |
| **Fibonacci** | Auto-computed retracement + extension levels from 52-week H/L |
| **Arbitrage** | TASE/NYSE price gap detection for dual-listed Israeli stocks, live USD/ILS rate |
| **Fundamentals** | P/E, EPS, dividend yield, analyst target, market cap, insider transactions, competitors |
| **News Sentiment** | Google News RSS + Yahoo Finance → score -1.0 to +1.0 with source diversity |
| **Telegram Bot** | Full English analysis on demand + automated pre-market and post-close reports |
| **Interactive Charts** | Candlestick + Volume + RSI + Fibonacci published to GitHub Pages |
| **Streamlit Dashboard** | Local browser UI for ad-hoc analysis without Telegram |

**Dual-listed stocks supported:** `TEVA`, `NICE`, `CHKP`, `AMDOCS`, `CEVA`, `GILT`, `RADCOM`, `TOWER`, `ORCL`

---

## Architecture

```
Telegram command / scheduled job
  → telegram_dispatcher.py
      → quant_engine.py       (price data + indicators)
      → news_search_agent.py  (sentiment via RSS)
      → fundamentals.py       (yfinance profile + insider data)
      → publisher.py          (Plotly chart → GitHub Pages)
  → HTML message + InlineKeyboard → user

Infrastructure:
  PostgreSQL  — price history, alerts, insider transactions, sentiment records
  Redis       — quote cache (60s), news cache (15m), fundamentals cache (4h)
  MCP :8001   — Google Search (optional)
  MCP :8002   — SQL query (optional)
  Streamlit :8501 — local dashboard
```

**Design principles:** Stateless agents (state in Postgres/Redis), fail loud (structured logging via structlog), security by default (no secrets in code).

---

## Prerequisites

| Requirement | Minimum |
|---|---|
| Python | 3.13+ |
| Docker + Docker Compose | Docker 24+ |
| Telegram Bot Token | from [@BotFather](https://t.me/BotFather) |
| GitHub Token | PAT with `repo` scope — for chart publishing |

---

## Installation & Startup

### Option 1 — Docker (recommended)

```bash
git clone https://github.com/maaoor6/MarketMind-Pro.git
cd MarketMind-Pro

cp .env.example .env
# Edit .env and fill in TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, GITHUB_TOKEN

docker compose up -d
```

Services available after startup:

| Service | URL | Description |
|---|---|---|
| Streamlit Dashboard | http://localhost:8501 | Local chart UI |
| Google MCP | http://localhost:8001 | News search MCP |
| SQL MCP | http://localhost:8002 | DB query MCP |
| PostgreSQL | localhost:5432 | |
| Redis | localhost:6379 | |

### Option 2 — Local dev

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
docker compose up -d postgres redis
cp .env.example .env  # edit with your values
alembic upgrade head
python -m src.agents.telegram_dispatcher
```

---

## Daily Operations

| Action | Command |
|---|---|
| Start | `docker compose up -d` |
| Stop | `docker compose down` |
| Rebuild after code change | `docker compose up -d --build` |
| Status | `docker compose ps` |
| Logs | `docker compose logs -f app` |
| Run migrations | `docker compose run --rm migrate` |
| DB shell | `docker compose exec postgres psql -U marketmind -d marketmind` |
| Full reset (deletes all data) | `docker compose down -v` |

---

## Telegram Bot

### Commands

| Command | Description |
|---|---|
| `/start` | Welcome menu with NYSE status + countdown, inline action buttons |
| `/analyze AAPL` | Full report: price + daily %, RSI, MACD, MAs, Fibonacci, fundamentals, insider trades, news sentiment, interactive chart button |
| `/news AAPL` | Top 5 live headlines with snippets and source labels; shows global market snapshot if no ticker given |
| `/fibonacci AAPL` | 52-week Fibonacci retracement + extension levels with nearest support/resistance |
| `/compare AAPL MSFT` | Side-by-side comparison: price, RSI, MACD, Fibonacci trend, P/E, EPS, market cap |
| `/health` | System dashboard: DB, Redis, MCP servers, news RSS connectivity |

Unrecognized text messages trigger a fallback inline menu.

### Automated Reports (APScheduler)

| Time (ET) | Days | Content |
|---|---|---|
| **9:00 AM ET** | Mon–Fri | Pre-market preview: AAPL, MSFT, NVDA, SPY, QQQ with price + RSI + global snapshot |
| **4:15 PM ET** | Mon–Fri | Post-close summary: AAPL, MSFT, NVDA, GOOGL, SPY with full signals |

### Sample `/analyze AAPL` output

```
📊 ANALYSIS — AAPL
━━━━━━━━━━━━━━━━━━━
💰 Current Price: $213.49  📈 +1.42%
🕐 Updated: 07/04/2026 14:30 ET

🏢 Apple Inc. (AAPL) — NASDAQ
🏭 Technology | Consumer Electronics | 👥 150,000 employees
💰 Market Cap: $3.2T

📊 Valuation Metrics:
  P/E (Trailing): 33.2x    P/E (Forward): 29.1x
  EPS (Trailing): $6.43    EPS (Forward):  $7.32
  Dividend Yield: 0.44%
  🎯 Analyst Target: $240.00

📈 52-Week Range: $164.08 – $237.49
🆚 Competitors: MSFT, GOOGL, META

📉 Technical Indicators:
  RSI(14): 48.3  ⚪ Neutral
  MACD Line: +0.2841  |  Signal: +0.1923
  MACD Histogram: +0.0918  📈 Bullish
  Volume Spike: ❌ No

📏 Moving Averages:
  SMA_20: $210.14  ↑ Above
  SMA_50: $225.67  ↓ Below
  SMA_200: $203.45  ↑ Above

📐 Fibonacci (52-week):
  High: $237.49  |  Low: $164.08
  Uptrend 📈  |  Position: 67.3% from low
  🟢 Nearest Support:  $207.10
  🔴 Nearest Resistance: $216.38

🕵️ Insider Transactions — AAPL
  🔴 SELL — Timothy Cook (CEO)
  15/03/2026: 200,000 shares @ $219.50  |  Total: $43,900,000

📰 News Sentiment: 🟢 +0.38 (14 articles)

🌍 Market Status:
  🇺🇸 NYSE: 🟢 Open

[📊 Interactive Chart]  [📰 News]
```

### Market Snapshot (`/news` without ticker)

Shows ETF-based global snapshot grouped by category:
- **Equities**: SPY, VOO, QQQ, DIA, IWM, RSP
- **Currency/Vol**: DX-Y.NYB (DXY), ^VIX
- **Fixed Income**: TLT, AGG
- **Commodities**: GLD, SLV, USO
- **Crypto**: BTC-USD, ETH-USD

---

## Streamlit Dashboard

Run locally at http://localhost:8501:

```bash
streamlit run src/ui/dashboard.py
```

Sidebar controls: ticker input, period (3mo–5y), MA selection, Fibonacci toggle.
Main panels: key metrics row, RSI/MACD/volume-spike signals, 3-panel dark-mode chart (candlestick+MAs, volume, RSI), Fibonacci retracement + extension tables with nearest support/resistance.

---

## Environment Variables

Copy `.env.example` to `.env` and fill in your values.

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | ✅ | PostgreSQL async (`postgresql+asyncpg://...`) |
| `DATABASE_URL_SYNC` | ✅ | PostgreSQL sync for Alembic (`postgresql+psycopg2://...`) |
| `REDIS_URL` | ✅ | Redis connection string |
| `TELEGRAM_TOKEN` | ✅ | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | ✅ | Target chat or channel ID |
| `GITHUB_TOKEN` | ✅ | PAT with `repo` scope — for chart publishing |
| `GITHUB_PAGES_REPO` | ✅ | `owner/repo` format (default: `maaoor6/MarketMind-Pro`) |
| `EXCHANGERATE_API_KEY` | ⚠️ | Live USD/ILS rate (falls back to 3.72 if absent) |
| `GOOGLE_API_KEY` | ⚠️ | Google Custom Search API — news tier 2 |
| `GOOGLE_SEARCH_ENGINE_ID` | ⚠️ | Required with `GOOGLE_API_KEY` |
| `ALPHA_VANTAGE_KEY` | ❌ | Backup data source |
| `QUOTE_CACHE_TTL` | ❌ | Seconds, default 60 |
| `NEWS_CACHE_TTL` | ❌ | Seconds, default 900 |

**⚠️ = recommended | ❌ = optional**

---

## Testing

```bash
# Unit tests (no Docker needed)
pytest tests/unit/ -v

# With coverage report
pytest tests/unit/ --cov=src/quant --cov-report=term-missing

# Integration tests (requires Docker services)
docker compose up -d postgres redis
pytest tests/integration/ -m integration -v
```

Current status: **47 unit tests passing** ✅ | Minimum 80% coverage on `src/quant/`

---

## CI/CD

GitHub Actions (`.github/workflows/ci.yml`) runs on every push to `main`:

1. `ruff check` — lint
2. `black --check` — format
3. `bandit -r src/ -ll` — security scan (blocks on MEDIUM+)
4. `pytest tests/unit/` — unit tests with coverage
5. Integration tests (with Postgres + Redis service containers)
6. Docker arm64 build validation

Install local pre-commit hooks: `bash scripts/install_hooks.sh`

---

## Market Hours

| Market | Days | Hours | Notes |
|---|---|---|---|
| **NYSE/NASDAQ** | Mon–Fri | 9:30 AM – 4:00 PM ET | |
| **TASE** | Mon–Thu | 10:00 AM – 5:25 PM IL | Pre-open 9:45 AM |
| **TASE** | Friday | 10:00 AM – 3:45 PM IL | Early close |

---

## License

MIT © 2026 MarketMind-Pro

> **Disclaimer:** For research and analysis purposes only. Not investment advice.
