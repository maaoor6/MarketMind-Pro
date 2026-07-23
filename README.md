# MarketMind-Pro 📈

> Autonomous trading intelligence for US markets and TASE (Tel Aviv Stock Exchange) — real-time quantitative analysis, fundamentals, news sentiment, and interactive charts via Telegram.

[![Python](https://img.shields.io/badge/Python-3.13+-blue?logo=python)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Apple%20Silicon%20M4-black?logo=apple)](https://apple.com)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Version](https://img.shields.io/badge/Version-1.8.0-orange)]()

---

## What It Does

| Capability | Description |
|---|---|
| **Technical Analysis** | RSI, MACD, SMA/EMA (20–200), volume spike detection, momentum score |
| **Fibonacci** | Auto-computed retracement + extension levels from 52-week H/L |
| **Arbitrage** | TASE/NYSE price gap detection for dual-listed Israeli stocks, live USD/ILS rate |
| **Fundamentals** | P/E, EPS, dividend yield, analyst target, market cap, insider transactions, competitors |
| **Earnings** | EPS and revenue vs estimate with beat/miss flags, YoY revenue growth |
| **Sector Rotation** | 11 SPDR S&P 500 sector ETFs ranked by daily % change |
| **Live Prices** | Pre-market 🌅 and after-hours 🌙 prices via yfinance fast_info — not just last close |
| **News Sentiment** | Google News RSS + Yahoo Finance → score -1.0 to +1.0 with source diversity |
| **Telegram Bot** | Full English analysis on demand + automated pre-market and post-close reports |
| **Price Alerts** | `/setalert` — above/below price alerts checked every 5 minutes |
| **Autonomous Paper Trading** | Multi-agent Orchestrator (8 backtest-promoted strategies in thematic agents) trading a simulated $10k StockArena account with a zero-cost macro/market-timing gate, dynamic stock discovery, evidence-based weighting that disables proven losers, per-agent circuit breakers, ATR sizing, risk management, and a Telegram kill-switch |
| **Backtesting** | Offline walk-forward backtests: 24 strategies over up to ~60 years across a ~435-ticker pool, plus stress (2008/2020/2022) and Monte-Carlo overfitting checks; results seed the live agent's strategy weights |
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

Autonomous trading (runs alongside the bot in src/main.py):
  trading_agent.py → src/trading/ (strategies, allocator, risk, universe scanner)
    → StockArena REST API (paper trading, simulated money)

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

## Autonomous Paper Trading (simulated money)

An optional trading agent runs alongside the bot and paper-trades a **$10k simulated
StockArena account** — no real money is ever involved. Every cycle (default 5 minutes) it
builds a quality-filtered universe (screener candidates must be in a long-term uptrend, blended half-half with a stable watchlist core), runs 8 backtest-promoted strategies
(trend following, breakout, dip buying, gap momentum, multi-timeframe alignment, Bollinger
reversion, 12-month momentum, 52-week-high momentum) over daily/weekly/monthly timeframes,
weights capital toward proven winners — a strategy whose measured average turns negative is
disabled automatically until it recovers — ranks buy ideas across all tickers by evidence,
sizes positions down in high-volatility names and during portfolio drawdowns, lets
long-horizon winners run past the profit cap on a trailing stop, and applies strict risk
management: stop-loss, trailing stop, take-profit, daily circuit breaker, horizon-scaled minimum
holding periods, position/cash limits, and commission awareness.

The strategies are organized into **thematic agents** (Core Momentum, Low-Vol Quality,
Defensive, Macro Rotation, Seasonality), coordinated by an **Orchestrator** that arbitrates
the single shared portfolio — activating the right agents per regime (Defensive in bear
markets), capping each agent's capital and any one sector's exposure, and sizing positions to
an equal dollar-at-risk. A **macro / market-timing gate** (strictly zero paid-API cost:
SPY/QQQ trend, VIX + term structure, yield curve, Fed net liquidity, cross-asset momentum,
FOMC/CPI/NFP blackout windows, and a local FinBERT/VADER news-sentiment overlay) decides
*whether* it's a good time to trade at all — halting new buys in risk-off tapes while exits
still run, with anti-whipsaw hysteresis. A per-agent circuit breaker freezes any single agent
that draws down too far, and an execution tracker watches live-fill vs. decision-price drift.

It is **off by default** (`TRADING_ENABLED=false`, `MACRO_GATE_ENABLED=false`) and can be
halted at any time from Telegram with `/trading off` (or `/force_macro_off on`). Every trade
is persisted and reported to the admin chat.

---

## Backtesting (offline, local, free)

Test 24 trading strategies — the live agent's 8 promoted winners plus 16 experimental
(backtest-only) ones, including three new-method strategies (sector rotation, cross-asset
intermarket, seasonality) — and the agent simulations (all-strategy research agent, the exact
live configuration, an optional macro-gated variant, and walk-forward versions), on up to ~60
years of historical daily data, entirely on your own machine. The candidate pool spans ~435
tickers: US large caps in every sector, growth/cloud names, foreign ADRs, the Israeli
dual-listed stocks (TEVA, NICE, CHKP…), REITs and sector ETFs. No Docker, no Redis, no
database, no paid APIs: data comes from yfinance (cached locally as parquet), execution is a
fully simulated broker with commissions + slippage, and **no order is ever sent anywhere**.

Beyond the standard runs, `--mode stress` replays the agent (gated vs. ungated) through the
2008/2020/2022 crisis windows with a slippage shock, and `--mode montecarlo` bootstraps the
trade sequence into a return distribution so you can tell a real edge from an overfit one
(a strategy whose 5th-percentile CAGR stays positive is robust). `--macro-gate` adds a
market-timing-gated A/B variant to the main report.

```bash
bash scripts/backtest.sh            # start in background; the Hebrew report opens in your browser when done
bash scripts/backtest.sh status     # running? + latest report
bash scripts/backtest.sh stop       # stop a running backtest — nothing is left behind
```

By default `backtest.sh` runs the FULL ~435-ticker pool (`--tickers ALL`); you can also
rotate the least-tested names (`--rotate 8`) or pass your own
(`python -m src.backtest --tickers AAPL MSFT SPY`). Strategies are validated **walk-forward**
(trained on one window, tested on unseen data), and appends the results to a cumulative
history (`data/backtest_history.json`). The layman-friendly Hebrew report lands in
`reports/backtest_<date>.html`, and `reports/index.html` aggregates every run into an
all-time strategy ranking.

Most importantly, the accumulated results teach the live bot: `--export-weights` (or the
"אמץ משקולות" button in Telegram) writes `data/backtest_weights.json`, which the live
`StrategyAllocator` uses as regime-aware (bull/bear/volatile) starting weights until real
trading data takes over. Known limitation: the candidate pool only contains tickers that
exist today (survivorship bias), so absolute returns skew slightly optimistic.

---

## Telegram Bot

### Commands

| Command | Description |
|---|---|
| `/start` | Welcome menu with NYSE status + countdown, inline action buttons |
| `/analyze AAPL` | Full report: live price (🌅/🌙 session label), RSI, MACD, MAs, Fibonacci, fundamentals, earnings, insider trades, news sentiment, interactive chart button |
| `/news AAPL` | Top 5 live headlines with snippets and source labels; shows global market snapshot + sector rotation if no ticker given |
| `/fibonacci AAPL` | 52-week Fibonacci retracement + extension levels with nearest support/resistance |
| `/compare AAPL MSFT` | Side-by-side comparison: price, RSI, MACD, Fibonacci trend, P/E, EPS, market cap |
| `/sectors` | S&P 500 sector rotation: 11 SPDR ETFs ranked by daily % change with breadth count |
| `/setalert AAPL 220` | Set a price alert — direction (above/below) auto-detected from the current price |
| `/myalerts` | List your active price alerts |
| `/cancelalert AAPL` | Cancel your alert(s) for a ticker |
| `/portfolio` | Live paper-trading portfolio: cash, value, return, positions, strategy weights, last decisions + macro-gate state, per-agent status, execution drift — **admin only** |
| `/trading on\|off\|status` | Autonomous trading kill-switch + status — **admin only** |
| `/force_macro_off on\|off\|status` | Manual macro buy-halt override + current macro-gate regime/decision — **admin only** |
| `/pause_agent <name>` · `/resume_agent <name>` | Freeze/unfreeze one thematic agent (Core Momentum, Defensive, …) — **admin only** |
| `/sync_positions` | Force a StockArena reconciliation on the next cycle — **admin only** |
| `/backtest [TICKERS...]` | Run the offline strategy backtest in the background; replies with the winning strategy + local Hebrew report path, with a one-tap button to adopt the learned weights — **admin only** |
| `/health` | System dashboard: DB, Redis, MCP servers, news RSS, quant engine + trading bot status — **admin only** |

Unrecognized text messages trigger a fallback inline menu.

### Automated Reports (APScheduler)

| Time | Days | Content |
|---|---|---|
| **9:00 AM ET** | Mon–Fri | Pre-market preview: AAPL, MSFT, NVDA, SPY, QQQ with live price + RSI + global snapshot + sector rotation |
| **4:15 PM ET** | Mon–Fri | Post-close summary: AAPL, MSFT, NVDA, GOOGL, SPY with full signals + global snapshot + sector rotation |
| **Every 5 min** | Mon–Fri | Price alert check — triggered alerts notify you and auto-deactivate |
| **6:00 PM IL** | Sunday | Weekly strategy learning report: weights, avg returns, disabled strategies, portfolio vs SPY (when autonomous trading is enabled) |

### Sample `/analyze AAPL` output

```
📊 ANALYSIS — AAPL
━━━━━━━━━━━━━━━━━━━
💰 Current Price: $213.49  📈 +1.42% 🌅 (pre-market)
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
| `TELEGRAM_CHAT_ID` | ✅ | Target chat or channel ID (also receives unauthorized access alerts) |
| `GITHUB_TOKEN` | ✅ | PAT with `repo` scope — for chart publishing |
| `GITHUB_PAGES_REPO` | ✅ | `owner/repo` format (default: `maaoor6/MarketMind-Pro`) |
| `ALLOWED_USER_IDS` | ⚠️ | Comma-separated Telegram user IDs allowed to use the bot. Leave empty for open mode |
| `EXCHANGERATE_API_KEY` | ⚠️ | Live USD/ILS rate (falls back to 3.72 if absent) |
| `GOOGLE_API_KEY` | ⚠️ | Google Custom Search API — news tier 2 |
| `GOOGLE_SEARCH_ENGINE_ID` | ⚠️ | Required with `GOOGLE_API_KEY` |
| `ALPHA_VANTAGE_KEY` | ❌ | Backup data source |
| `QUOTE_CACHE_TTL` | ❌ | Seconds, default 60 |
| `NEWS_CACHE_TTL` | ❌ | Seconds, default 900 |
| `TRADING_ENABLED` | ❌ | Autonomous paper trading master switch — **default `false`** |
| `STOCK_ARENA_TOKEN` | ⚠️ | StockArena API token — required only if trading is enabled |
| `TRADING_*` / risk knobs | ❌ | Universe filters, position sizing, stop-loss/take-profit, ATR sizing, sector cap, etc. — see `.env.example` |
| `MACRO_GATE_ENABLED` | ❌ | Macro / market-timing gate master switch — **default `false`** |
| `FRED_API_KEY` | ❌ | Free FRED key for yield curve + Fed net liquidity (fails open if unset) |
| `SENTIMENT_LOCAL_ENABLED` / `MACRO_*` / `AGENT_BREAKER_*` | ❌ | Local sentiment overlay, blackout/hysteresis, per-agent circuit breaker — see `.env.example` |
| `BACKTEST_WEIGHTS_PATH` | ❌ | Cold-start weight seeds from the backtest (default `data/backtest_weights.json`) |

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

Current status: **351 unit tests passing** ✅ | Minimum 80% coverage on `src/quant/`

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
