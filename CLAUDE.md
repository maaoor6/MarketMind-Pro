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

# Backtest (offline, local, no Docker/Redis/DB — yfinance + parquet cache only)
bash scripts/backtest.sh              # = start: FULL pool (~435 tickers) in the background, opens the Hebrew report when done
bash scripts/backtest.sh status       # running? + latest report
bash scripts/backtest.sh stop         # kill a running backtest
python -m src.backtest --mode all --tickers ALL --export-weights   # full pool (~435)
python -m src.backtest --mode all --rotate 8 --export-weights      # quick rotation
python -m src.backtest --tickers AAPL MSFT SPY --start 1996-01-01
python -m src.backtest --mode sweep   # walk-forward parameter sweep
python -m src.backtest --mode all --macro-gate --tickers ALL   # + macro-gated A/B variant
python -m src.backtest --mode stress --tickers ALL     # 2008/2020/2022 crisis stress (gated vs ungated)
python -m src.backtest --mode montecarlo --tickers ALL # bootstrap trade sequence → overfitting check

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
| `/analyze [TICKER]` | `cmd_analyze` | Full report: live price (pre-market/after-hours aware), RSI, MACD, Fibonacci, fundamentals, insider trades, earnings, news sentiment, chart button |
| `/news [TICKER]` | `cmd_news` | Top 5 live headlines with snippets; shows global market snapshot + sector rotation if no ticker given |
| `/fibonacci [TICKER]` | `cmd_fibonacci` | 52-week Fibonacci retracement + extension levels with support/resistance |
| `/compare T1 T2` | `cmd_compare` | Side-by-side comparison table (price, RSI, MACD, Fibonacci, sentiment) |
| `/sectors` | `cmd_sectors` | S&P 500 sector rotation: 11 SPDR ETFs ranked by daily % change with breadth count |
| `/setalert TICKER PRICE` | `cmd_setalert` | Set a price alert; auto-detects `PRICE_ABOVE` vs `PRICE_BELOW` from the current price, stored in PostgreSQL `UserAlert` |
| `/myalerts` | `cmd_myalerts` | List the caller's active alerts |
| `/cancelalert TICKER` | `cmd_cancelalert` | Deactivate the caller's alert(s) for a ticker |
| `/portfolio` | `cmd_portfolio` | Live StockArena paper-trading portfolio: cash, value, return, positions, strategy weights, last decisions — **admin only** |
| `/trading on\|off\|status` | `cmd_trading` | Autonomous trading kill-switch (Redis `trading:enabled`) + status — **admin only** |
| `/backtest [TICKERS...]` | `cmd_backtest` | Trigger the offline strategy backtest (background thread; no args → rotation of 8 least-tested tickers). Replies with the winner + local Hebrew report path; "📥 אמץ משקולות" button exports cumulative seeds — **admin only** |
| `/force_macro_off on\|off\|status` | `cmd_macro` | Manual macro buy-halt override + current macro-gate state (regime/decision/size). Writes Redis `trading:macro:force_off` — **admin only** |
| `/pause_agent <name>` / `/resume_agent <name>` | `cmd_pause_agent` | Freeze/unfreeze one thematic agent (writes `trading:agent:{name}:halted_until`) — **admin only** |
| `/sync_positions` | `cmd_sync_positions` | Force a StockArena reconciliation on the next Orchestrator cycle (Redis `trading:force_reconcile`) — **admin only** |
| `/health` | `cmd_health` | System dashboard: DB, Redis, MCP, news RSS connectivity, quant engine + trading bot status — **admin only** (`TELEGRAM_CHAT_ID`) |
| _(any text)_ | `cmd_fallback` | Unrecognized messages → quick-action inline keyboard |

**Inline keyboard callbacks** (`callback_handler`): `analyze:TICKER`, `fib:TICKER`, `news:TICKER`, `health`, `market_open`, `prompt_analyze`, `prompt_news`, `prompt_fibonacci`, `prompt_compare`, `portfolio`, `trading_status`, `trading_on`, `trading_off`, `bt:adopt` (admin — write backtest weight seeds).

**Access control** (`build_application()`): If `ALLOWED_USER_IDS` is set, a `filters.User(user_id=[...])` filter is applied to all `CommandHandler` registrations. `callback_handler` performs its own check at the top. `_cmd_unauthorized` handles all blocked requests — replies with the user's ID and notifies the admin. `/health` has an additional check inside `cmd_health` — only `TELEGRAM_CHAT_ID` (the admin) can use it, even if other users are on the whitelist.

`_parse_user_ids` (in `config.py`) — `BeforeValidator` that handles empty string, single int, and comma-separated string → `list[int]`. `env_ignore_empty=True` in `SettingsConfigDict` ensures empty env vars fall back to field defaults.

### Scheduled Jobs (APScheduler via python-telegram-bot JobQueue)
- **9:00 AM ET, Mon–Fri** — `_job_market_preview`: pre-market snapshot for `[AAPL, MSFT, NVDA, SPY, QQQ]`
- **4:15 PM ET, Mon–Fri** — `_job_market_close_regular`: post-close summary for `[AAPL, MSFT, NVDA, GOOGL, SPY]`
- **Every 5 min** — `_job_check_alerts`: evaluates active `UserAlert` price alerts and notifies + deactivates triggered ones
- **Sunday 18:00 IL** — `_job_weekly_strategy_report`: per-strategy weights / avg return / win rate, disabled (weight-0) strategies, portfolio vs SPY-this-week line (skipped when `TRADING_ENABLED=false`)

### QuantEngine (`src/agents/quant_engine.py`)
- `analyze(ticker) → QuantSignal` — full technical analysis (RSI, MACD, Fibonacci, volume spike, market status); overrides `signals["price"]` and `signals["prev_close"]` with live quote
- `fetch_price_data(ticker, period, interval) → pd.DataFrame` — yfinance with Redis caching
- `fetch_live_price(ticker) → tuple[float, float | None]` — returns `(last_price, prev_close)` via `yf.Ticker.fast_info`; works in pre-market, regular, and after-hours sessions
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
- `bollinger(prices, period=20, num_std=2.0) → BollingerResult`
- `atr(high, low, close, period=14) → pd.Series` — Wilder's Average True Range
- `adx(high, low, close, period=14) → ADXResult` — trend strength + `plus_di`/`minus_di` (Wilder)
- `supertrend(high, low, close, period=10, multiplier=3.0) → pd.Series` — ±1 trend direction (causal carry-forward)
- `all_moving_averages(prices) → dict` — SMA + EMA for periods 20, 50, 100, 150, 200
- `generate_signals(prices, volume, high=None, low=None) → dict` — comprehensive signal dict (used for caching); ATR-family fields populated only when High/Low supplied
- `momentum_score(signals, closes) → MomentumScore` — composite momentum rating based on RSI, MACD, and MA alignment

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
- `fetch_earnings_report(ticker) → EarningsReport | None` — most recent earnings: EPS actual/estimate/surprise, revenue, beat/miss flags
- `is_reporting_today(ticker) → bool` — True if earnings date is today (ET)
- `was_reported_today(ticker) → bool` — True if earnings were released today
- `get_competitors(ticker) → list[str]` — static map for TEVA, AAPL, MSFT, NVDA, GOOGL, etc.
- `format_profile_english(profile) → str` — HTML for Telegram
- `format_insiders_english(ticker, txns) → str` — HTML for Telegram
- `format_earnings_english(report) → str` — HTML for Telegram

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
ETF-based (not raw futures), grouped for display. All prices are live via `yf.Ticker.fast_info.last_price` (pre-market / regular / after-hours), with fallback to daily close:
- **Equities**: SPY, VOO, QQQ, DIA, IWM, RSP
- **Currency/Vol**: DX-Y.NYB (DXY), ^VIX
- **Fixed Income**: TLT, AGG
- **Commodities**: GLD, SLV, USO
- **Crypto**: BTC-USD, ETH-USD

Groups (`_EQUITY_ETFS`, `_CURRENCY_VOL`, `_FIXED_INCOME`, `_COMMODITY`, `_CRYPTO`) drive blank-line separators in output.
Session label (`_session_label()`) appended to snapshot and sector headers: 🌅 pre-market, 🌙 after-hours, blank during regular hours.

### Sector Rotation (`telegram_dispatcher.py → _SECTOR_ETFS`)
11 SPDR sector ETFs ranked by daily % change. Cached in Redis as `sectors:daily` (TTL 600s). Included in `/news` (no ticker), morning preview, and post-close reports.
- ETFs: XLK, XLV, XLF, XLE, XLI, XLY, XLP, XLC, XLB, XLRE, XLU
- Emoji thresholds: 🔥 >+1.5%, 🟢 ≥0%, 🟡 ≥-0.5%, 🔴 <-0.5%

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
| `TradeRecord` | ticker, action (BUY/SELL), quantity, fill_price, notional, strategy, reason, portfolio_value_after, cash_after, executed_at |
| `StrategySignalRecord` | strategy, ticker, action, confidence, decision_price, eval_after, scored, virtual_return_pct |
| `StrategyPerformance` | strategy (unique), signals_scored, avg_return_pct, win_rate |

### Autonomous Trading Agent (`src/agents/trading_agent.py` + `src/trading/`)
Paper-trades a **simulated-money** StockArena account ($10k) via REST API. Runs as a third task in `main.py` next to QuantEngine.

**Components** (`src/trading/`):
- `stockarena_client.py` — `StockArenaClient`: typed async REST client. Envelope unwrap (`{"success", "data"|"error"}`), retries `RATE_LIMITED`/`MARKET_DATA_UNAVAILABLE` with backoff, raises `FatalTradingError` (never retried) on `UNAUTHORIZED`/`FORBIDDEN`/`BOT_INACTIVE`, `TradeTimeoutError` on trade-POST timeout (order may have filled — must reconcile, never blind-retry). Token from `STOCK_ARENA_TOKEN`, never logged.
- `strategies.py` — pure strategies over 3 timeframes. **Live set** (`default_strategies()`, promoted 2026-07-09 from the 64-year full-pool backtest — every member CAGR ≥5.8%, Sharpe ≥0.56): `TrendFollowing` (1wk: SMA50>SMA200 + weekly MACD), `Breakout` (1mo: resistance break on volume spike), `DipBuyer` (price>SMA200 but <SMA20 with RSI<45; 72h), `GapMomentum` (gap-up ≥2% + volume spike + weekly MACD), `MultiTimeframe` (weekly+monthly MACD bullish + price>SMA100 + momentum ≥50), `BollingerReversion` (close below lower band in uptrend; exit at middle band; 72h), `TSMomentum` (12−1-month return positive + price>SMA200; 480h — the overall backtest winner, 11.5% CAGR), `FiftyTwoWeekHigh` (George & Hwang: within 2% of 52w high + 6m return>0 + monthly MACD; 480h), and the two 2026-07-23 promotions forming the **Macro Rotation agent** — `SectorRotation` (cross-sectional relative-momentum across the 11 SPDR sector ETFs — `ctx.cross_section["sector_rank"]` live, absolute 3m-momentum fallback; 480h) and `CrossAsset` (intermarket trend on TLT/IEF/AGG/GLD/SLV/USO/DBC/UUP; 480h), which top the avg-per-signal metric (+1.08%, +0.99%) and lead in BEAR/VOLATILE. **Experimental set** (`experimental_strategies()`, backtest-only — promoting one to live = moving it into `default_strategies()`; demoted live strategies keep being measured here): `MomentumDaily` + `MeanReversion` (demoted 2026-07-09, CAGR ≤4.5%), `MACDCross`, `RSI2Reversion` (Connors RSI-2), `DonchianBreakout` (Turtle 20/10), `DoubleSeven` (Connors: close = 7-day low above SMA200; exit at 7-day high; 72h), `PullbackSMA50` (±2% of SMA50 in uptrend with RSI<50; 120h), `GoldenCross` (fresh SMA50/200 cross events via `sma_50_prev`/`sma_200_prev`; 480h), `VolContraction` (BB width ≤6% squeeze + 20-day channel break + weekly MACD; 120h), `LowVolTrend` (low-vol anomaly: `vol_20d`<25% + price>SMA200 + 6m return>0; 480h), `Supertrend` (ATR trend flip above SMA200; 120h), `AdxTrend` (Wilder ADX>25 + +DI>−DI + price>SMA50; 168h), `KeltnerBreakout` (close above EMA20+2×ATR channel + weekly MACD; exit below EMA20; 120h), and the **new-method** `Seasonality` (turn-of-month + Nov–Apr calendar tilt via `ctx.as_of`, only in an uptrend; 120h — added 2026-07-22, kept experimental for its deep −54% maxDD). All fail-closed to HOLD; thresholds are class attrs (sweepable). The ATR-family fields (`atr_14`, `atr_pct`, `adx_14`, `plus_di`, `minus_di`, `supertrend_dir`, `keltner_upper/lower`) need High/Low — `generate_signals(prices, volume, high, low)` populates them (None without OHLC). `StrategyContext` also carries `as_of` (bar date) and `cross_section` (per-cycle cross-sectional data). `StrategySignal` carries `volatility` + `atr_pct` for sizing. `STRATEGY_HORIZON_HOURS` covers both sets (24 strategies total: 10 live + 14 experimental).
- `allocator.py` — `StrategyAllocator`: persists every non-HOLD signal, scores virtual returns after per-strategy horizons, weights capital toward proven winners. **Evidence-sharpened weights** (`compute_weights`, shared with the backtest sim): `1 + WEIGHT_SENSITIVITY×avg_return` (default ×50 — per-signal averages are fractions of a percent, so without amplification weights are near-uniform and learning never bites), floored at 0.1, capped at 3.0; a strategy with a **negative average is disabled (weight 0)** — its signals keep being recorded/scored so it re-enables automatically when its average recovers; cold-start gets the mean of enabled raws.
- `universe.py` — `UniverseScanner`: dynamic stock discovery. Each cycle scans Yahoo Finance predefined screeners (`day_gainers`, `most_actives` — `day_losers` was removed 2026-07-18: negative selection; backtests showed the screener universe was why the combined agent lost while every strategy won standalone) via `yf.screen`, filters candidates (US common stock/ETF symbols only, price ≥ `TRADING_MIN_PRICE`, 3-month avg volume ≥ `TRADING_MIN_AVG_VOLUME`, market cap ≥ `TRADING_MIN_MARKET_CAP`, affordable within the position cap, **quality gate: price above the 200-day average**, fail-open without data). Final list = SPY + held positions pinned first, then a **stable watchlist core** (`UNIVERSE_QUALITY_RATIO` = 0.5 of the free slots) via `merge_with_core`, then dynamic candidates, capped at `TRADING_UNIVERSE_SIZE`. Result cached in Redis `trading:universe` (15 min). On scan failure or `TRADING_DYNAMIC_UNIVERSE=false` falls back to the static `TRADING_WATCHLIST`.
- `risk.py` — `RiskManager`: exits first (stop-loss −5%, trailing stop 4% below HWM once up ≥5%+fees, take-profit cap +10%+fees, Fib support break), daily circuit breaker (−3% intraday halts until next day), sizing (max 20%/position, 5 positions, 5% cash reserve, $500 min notional, extended-hours size ×0.5 + conf ≥0.7), full-exit sells only, never shorts, 30-min per-ticker cooldown. **Anti-churn**: strategy SELLs (never protective exits) are ignored while a position is younger than its minimum holding time — `max(MIN_HOLDING_HOURS (72h), the OPENING strategy's eval horizon)` via `min_holding_hours_for` (a ts_momentum entry holds ≥480h/20 bars; entry time in Redis `trading:entry_time:{ticker}`) — or when confidence < `SELL_CONFIDENCE_GATE` (0.6). This horizon-scaled holding was the final fix that flipped the combined agent to beating SPY in backtests. **Commission-aware**: `TRADE_COMMISSION` ($2.50/order) is deducted from spendable cash, profit-exit thresholds shift up by the round-trip fee's share of the position, and orders whose round-trip fee exceeds `MAX_COMMISSION_PCT` (1%) of notional are rejected. **Volatility-aware sizing**: `StrategySignal.volatility` (annualized 20d vol, auto-filled by `Strategy._signal` from `vol_20d`) shrinks the budget by `TARGET_POSITION_VOL/vol` when vol exceeds the 0.30 target (fail-open without data). **Drawdown brake**: `drawdown_brake_factor` tracks the portfolio's all-time HWM (Redis `trading:portfolio_hwm`); below `DRAWDOWN_BRAKE_PCT` (15%) from the HWM every new-buy budget is multiplied by `DRAWDOWN_SIZE_FACTOR` (0.5) until recovery. **Horizon-aware profit exits**: each buy records its opening strategy (Redis `trading:entry_strategy:{ticker}`); positions opened by ≥480h-horizon strategies (`ts_momentum`, `high_52w`, `low_vol_trend`, `golden_cross`) skip the +10% take-profit cap and ride the trailing stop only (`RiskManager.profit_cap_applies`).

**Decision flow per cycle (default 300s)**: portfolio → circuit breaker → dynamic universe scan (`UniverseScanner`) → per-ticker contexts (daily signals + live price + Fib + weekly/monthly + momentum) → protective exits → score matured signals → weights (per-regime) → per-ticker best signal (`confidence × weight`; **weight-0 = disabled strategies are filtered out**; a confident SELL beats BUY, gated by min-holding + confidence — see risk.py anti-churn) → **sells execute first (free cash), then buys ranked by `confidence × weight` across all tickers** — the strongest ideas get the capital, not scan order → regime filter (SPY<SMA200 or VIX>30 halves buy confidence) → vol-aware size → execute → persist + Telegram notify.

**Kill-switches**: agent trades only when `TRADING_ENABLED=true` (env) AND Redis `trading:enabled != "off"` (`/trading on|off`). Fatal API errors stop the loop permanently + admin alert. Startup reconciliation imports server trade history into `trade_records`.

**Backtest weight seeds**: `StrategyAllocator.get_weights(regime)` — cold-start strategies (fewer than 5 scored live signals) take their prior from `data/backtest_weights.json` (`load_weight_seeds`, values clamped ±20%, regime-specific `regime_avg_returns[BULL|BEAR|VOLATILE]` override the global `avg_returns`, fail-open to `{}`). The regime label comes from `TradingAgent._regime_state(contexts) → (factor, regime)` — the same SPY<SMA200 / VIX>30 signals as the confidence dampener. Weights cache key is per-regime: `trading:weights:{regime}`. Live scored data always wins once ≥5 signals exist.

### Multi-Agent Orchestrator & Macro-Timing Gate (`src/trading/orchestrator.py`, `agents.py`, `macro_gate.py`)
`main.py` runs the **`Orchestrator`** (a subclass of `TradingAgent`, reusing its lifecycle, StockArena client, `RiskManager`, `StrategyAllocator`, `UniverseScanner`, context builder, and execution path) in place of the flat agent. It replaces the single decision cycle with a multi-agent one over the **one shared StockArena portfolio**.

**Thematic agents** (`src/trading/agents.py` — `build_agents()` partitions whatever strategy set it's given, default `default_strategies()`; empty roles are dropped so new-method agents appear only once their strategies are promoted):
- `core_momentum` (ts_momentum, breakout, trend_following, high_52w, gap_momentum, …) — active BULL/VOLATILE, cap 0.70
- `low_vol_quality` (multi_timeframe, + low_vol_trend/pullback_sma50 once promoted) — active BULL/VOLATILE, cap 0.60
- `defensive` (dip_buyer, bollinger_reversion, + double_seven/mean_reversion once promoted — the BEAR specialists) — active BEAR/VOLATILE, cap 0.60
- `macro_rotation` (sector_rotation, cross_asset — new methods) — all regimes, cap 0.40
- `seasonality` (seasonality — new method) — BULL only, cap 0.30

**Decision flow per cycle** (`Orchestrator._trading_cycle`): manual `/sync_positions` reconcile → portfolio → circuit breaker → universe + contexts (+ `_inject_sector_ranks` for SectorRotation's `cross_section`) → protective exits → score matured signals → **`MacroTimingGate.evaluate()`** (or legacy `_regime_state` when `macro_gate_enabled=false`) → per-agent circuit-breaker update + frozen-agent set → per-ticker `_decide_orchestrated` (all signals recorded for learning; SELLs may come from any owned strategy, BUYs only from **active** agents; buy confidence × `MacroState.confidence_factor`) → sells first → **`decision == OFF` ⇒ no new buys** → buys ranked by conf×weight, sized × `MacroState.size_mult`, gated by **per-agent capital cap** + **cross-agent sector-exposure cap** → execute → persist `trading:orchestrator:state`.

**MacroTimingGate** (`src/trading/macro_gate.py`, strictly zero paid-API cost) → `MacroState{regime, decision(ON/SCALED/OFF), size_mult, confidence_factor, blackout, risk_score, rationale}`:
- `score_macro(MacroData)` — pure quantitative scoring (reused by the backtest) over `src/trading/macro_data.py` readings: SPY/QQQ vs SMA50/200, VIX + VIX9D/VIX term structure, yield curve (FRED `DGS10−DGS2`), MOVE, HYG/LQD credit trend, Fed net liquidity (FRED `WALCL−RRP−TGA`), DXY/gold/oil momentum. Every field fails open to neutral.
- **Blackout windows** (`src/trading/macro_calendar.py`) — FOMC (seeded) / CPI (seeded) / NFP (first-Friday, computed); within `MACRO_BLACKOUT_HOURS` reduces `size_mult` (fail-open beyond the seeded horizon).
- **Local sentiment overlay** (`src/trading/sentiment_local.py`, live-only, can only tighten) — FinBERT if `transformers`+`torch` installed → VADER → keyword fallback, over the existing RSS headlines; + CBOE put/call (best-effort). Never a paid LLM.
- **Hysteresis** — OFF applies immediately; recovery to ON needs `MACRO_HYSTERESIS_CYCLES` consecutive constructive cycles (streak in `trading:macro:stable_streak`).

**Per-agent circuit breaker** (`Orchestrator._update_agent_breakers`) — each agent's attributed holdings' value tracks a Redis HWM (`trading:agent:{name}:hwm`); a fall past `AGENT_BREAKER_DD_PCT` freezes only that agent for `AGENT_BREAKER_FREEZE_DAYS` (`trading:agent:{name}:halted_until`). **ATR dollar-at-risk sizing** (`RiskManager.size_buy`) — caps notional so an adverse `ATR_STOP_MULTIPLE×ATR` move risks ≤ `POSITION_RISK_PCT` of the portfolio (fail-open without ATR). **ExecutionTracker** (`src/trading/execution_tracker.py`) — records live-fill vs. decision-price drift to `trading:exec_drift`, surfaced in `/portfolio` + the weekly report. State (macro, agents, drift) is Redis-persisted for zero-downtime recovery.

### Backtesting Framework (`src/backtest/` — offline, local, free)
Standalone process (never touches StockArena/Redis/Postgres; yfinance + local parquet cache only). Tests all 24 strategies — the live 10 plus the 14 experimental ones — and the full agent (simulated with the expanded 24-strategy set) on up to ~60 years of daily bars, and feeds the results back to the live allocator as cold-start weight seeds. The live agent itself keeps trading only `default_strategies()` until a strategy is explicitly promoted. **Data-quality gate**: `validate.py` (`validate_history`, run inside `load_history`) strips corrupt LEADING segments — frozen runs (>60 identical closes) and adjustment artifacts (>250% single-bar jumps) — before any indicator sees them (a real audit found HUBB's 1,861-bar 1977–84 flat run + 886% artifact jump feeding every prior backtest); genuine history and real crashes (2008, JAZZ +210% in 2009) are preserved. Re-runnable audit: `python -m src.backtest.validate`. **Survivorship bias** is documented, not "fixed": the pool is 100% current survivors, so all returns are upward-biased — the Hebrew report shows a prominent caveat banner.

**Run**: `bash scripts/backtest.sh [start|status|stop]` (background + auto-opens the report; default = the FULL ~435-ticker pool), `/backtest` in Telegram (admin; rotation of 8), or `python -m src.backtest --mode {strategies,agent,walkforward,sweep,all,stress,montecarlo} [--tickers ...|ALL | --rotate N] [--start --end --cash --slippage-bps --export-weights --macro-gate --no-cache]`. `--macro-gate` adds a `full_agent_live_gated` A/B variant (live set with the market-timing gate). `--mode stress` reports gated-vs-ungated return/maxDD over 2008/2020/2022 crisis windows (3× slippage shock, `src/backtest/stress.py`). `--mode montecarlo` bootstraps the live agent's trade sequence into a CAGR/maxDD distribution — a strategy whose 5th-percentile CAGR stays positive is robust, not overfit (`src/backtest/montecarlo.py`). The macro gate is replayed offline by `src/backtest/macro_gate_sim.py` (quantitative core only; sentiment/blackout are live-only).

**Modules**:
- `data.py` — `load_history`/`load_universe`: yfinance daily `period="max"`, parquet cache in `data/cache/backtest/` (download-once); each load passes through `validate_history` (data-quality gate, see above; `reports=` collects per-ticker `DataQualityReport`s); `CANDIDATE_POOL` (~435 liquid names: large caps across all sectors, 2nd-tier semis/financials/healthcare, growth/cloud names (PLTR/SNOW/CRWD/UBER/…), alt-asset managers, foreign ADRs incl. TSM/ASML/NVO/BABA/MELI, the Israeli dual-listed stocks TEVA/NICE/CHKP/CEVA/GILT/RDCM/TSEM, REITs + sector/bond/commodity ETFs, plus the watchlist).
- `features.py` — `precompute_features`: all indicators vectorized once per ticker (causal rolling/ewm ≡ per-bar slices — verified by `test_backtest_features.py` equivalence tests); `build_context(ticker, feats, i, position)` assembles the exact live `StrategyContext` per bar; `BURN_IN_BARS=250`.
- `broker.py` — `SimulatedBroker`: in-memory fills with commission + slippage (0.05% default, adverse); reuses live `Position`/`Portfolio` dataclasses so `RiskManager.size_buy`/`validate_sell` work unchanged.
- `risk_sim.py` — `ExitEngine`: literal port of `RiskManager.check_exits`/trailing HWM with dicts instead of Redis; `circuit_breaker_tripped` daily-bar approximation.
- `allocator_sim.py` — `InMemoryAllocator`: mirrors record/score/weights with horizons in trading bars (24h→1, 72h→3, 120h→5, 168h→5, 480h→20); reuses `compute_weights`; `weights(regime)` prefers regime-conditional averages (≥5 scored in that regime) over global, mirroring live `get_weights(regime)`.
- `universe_sim.py` — `simulate_universe`: historical replay of `UniverseScanner` (per-day gainers/actives from the pool, same quality gate / stable core / merge / cap; survivorship-bias caveat documented).
- `engine.py` — `run_single_strategy` / `run_full_agent` (mirrors `_trading_cycle`/`_decide`: exits first, weight-0 strategies filtered, gated SELL beats BUY, buys ranked by conf×weight across tickers, regime-aware weights, regime factor, `weight_factor=min(1.5, w×n)`); same anti-churn gates as live (`min_holding_bars` from entry-fill bar + `SELL_CONFIDENCE_GATE`); decisions at close t, fills at open t+1; `run_benchmark` (SPY buy&hold). In `strategies` mode the standalone runs execute in parallel (fork `ProcessPoolExecutor`, sequential fallback). In `agent`/`all` modes runs `full_agent` (all 24), `full_agent_live` (the promoted 10 — the real live configuration), and `full_agent_live_full` (live set, no screener universe — the universe A/B); walk-forward likewise produces `walk_forward` + `walk_forward_live`. `--agent-universe {dynamic,full}` switches the primary sims' universe.
- `regimes.py` — BULL/BEAR (SPY vs SMA200) / VOLATILE (VIX>30) labels + per-regime metrics.
- `walkforward.py` — rolling 3y-train/1y-test windows; seeds learned in-sample, traded out-of-sample; stitched OOS equity is the honest "which strategy works" answer.
- `sweep.py` — walk-forward parameter grid over strategy class attrs (`MomentumDaily.buy_score`, `MeanReversion.rsi_oversold`, …) and risk settings; always restores defaults.
- `history.py` — `data/backtest_history.json`: every run appended; `aggregate()` = signal-count-weighted avg per strategy/regime across **all** runs (the seed source); `pick_rotation` favors least-tested tickers.
- `metrics.py` — CAGR/Sharpe/Sortino/maxDD/win-rate/profit-factor + `significance()` t-test labels (✅/⚠️/❌).
- `validate.py` — `validate_history` data-quality gate (frozen-run + adjustment-artifact trimming) + `summarize_reports` + `audit_pool` (`python -m src.backtest.validate`).
- `report.py` — Hebrew RTL browser report (`reports/backtest_<date>.html`, self-contained Plotly) incl. a survivorship-bias + data-cleaning caveat banner (`_integrity_banner`), the "מה הסוכן למד" cumulative verdict table, per-entry-strategy realized P&L attribution inside each agent sim (`metrics.attribute_pnl`), and agent-return-per-decade table + cumulative `reports/index.html` + `export_weight_seeds` → `data/backtest_weights.json`.
- `runner.py` — `run_pipeline` (shared CLI/Telegram orchestration), `run_backtest_async` (thread executor for the bot).

**Outputs (all local, gitignored)**: `reports/*.html`, `data/backtest_history.json`, `data/backtest_weights.json`, `data/cache/backtest/*.parquet`.

### Redis Cache Keys
| Key | TTL | Source |
|-----|-----|--------|
| `quote:{ticker}:{timeframe}` | `settings.quote_cache_ttl` (60s default) | QuantEngine |
| `sentiment:{ticker}` | `settings.news_cache_ttl` (900s default) | NewsSearchAgent |
| `fundamentals:{ticker}` | 14400s (4h) | fundamentals.py |
| `sectors:daily` | 600s (10 min) | `_fetch_sector_data()` in telegram_dispatcher |
| `trading:enabled` | none | `/trading on\|off` kill-switch |
| `trading:weights:{regime}` | 3600s | StrategyAllocator (per BULL/BEAR/VOLATILE regime) |
| `trading:cooldown:{ticker}` | 30 min | RiskManager |
| `trading:hwm:{ticker}` | 30d | RiskManager trailing stop |
| `trading:entry_time:{ticker}` | 30d | RiskManager min-holding (anti-churn) |
| `trading:entry_strategy:{ticker}` | 30d | RiskManager horizon-aware profit exits |
| `trading:portfolio_hwm` | none | RiskManager drawdown brake |
| `trading:day_open_value:{day}` / `trading:halted_until:{day}` | 1–2d | daily circuit breaker |
| `trading:last_decision:{ticker}` | 3600s | TradingAgent audit trail |
| `trading:universe` | 900s (15 min) | UniverseScanner dynamic candidate list |
| `macro:data` | 900s (15 min) | MacroDataProvider raw macro readings |
| `trading:macro:state` | 3600s | MacroTimingGate — last MacroState (recovery) |
| `trading:macro:stable_streak` | 3600s | MacroTimingGate hysteresis streak |
| `trading:macro:force_off` | none | `/force_macro_off` manual buy-halt override |
| `trading:agent:{name}:halted_until` | freeze window | Per-agent circuit breaker / `/pause_agent` |
| `trading:agent:{name}:hwm` | 30d | Per-agent circuit-breaker high-water mark |
| `trading:orchestrator:state` | 3600s | Orchestrator snapshot (macro + agents) for `/portfolio` + recovery |
| `trading:exec_drift` | none | ExecutionTracker running fill-vs-decision drift |
| `trading:force_reconcile` | none | `/sync_positions` forced reconciliation flag |

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

**Optional (access control):**
- `ALLOWED_USER_IDS` — comma-separated Telegram user IDs allowed to use the bot (e.g. `123456789,987654321`). Leave empty for open mode. Unauthorized users get a `🔒 Access Denied` message + their user ID; the admin receives an `⚠️ Unauthorized Access Attempt` alert via `TELEGRAM_CHAT_ID`.

**Autonomous trading (StockArena, simulated money):**
- `STOCK_ARENA_TOKEN` — API bearer token (secret; rotate via the StockArena dashboard if exposed)
- `STOCK_ARENA_BASE_URL` / `STOCK_ARENA_BOT_ID` / `STOCK_ARENA_USER_ID` — defaults: chaiware.org/stock_arena, 2, 2
- `TRADING_ENABLED` — master kill-switch, **default false**; also gated live by Redis `trading:enabled` (`/trading on|off`)
- `TRADING_EXTENDED_HOURS` (default true), `TRADING_INTERVAL_SECONDS` (300)
- `TRADING_DYNAMIC_UNIVERSE` (default true) — agent picks its own stocks via Yahoo screeners; `TRADING_UNIVERSE_SIZE` (20), `TRADING_MIN_PRICE` (5.0), `TRADING_MIN_AVG_VOLUME` (1M), `TRADING_MIN_MARKET_CAP` ($2B), `UNIVERSE_QUALITY_RATIO` (0.5 — stable-watchlist share of the universe)
- `TRADING_WATCHLIST` — 15-ticker **fallback** list, used only when the scan fails or dynamic mode is off
- Risk knobs: `MAX_POSITION_PCT`, `MAX_OPEN_POSITIONS`, `MIN_TRADE_NOTIONAL` (500), `TRADE_COMMISSION` (2.5 $/order), `MAX_COMMISSION_PCT` (0.01), `CASH_RESERVE_PCT`, `STOP_LOSS_PCT`, `TAKE_PROFIT_PCT`, `TRAIL_STOP_PCT`, `MAX_DAILY_LOSS_PCT`, `TICKER_COOLDOWN_MINUTES`, `MIN_CONFIDENCE`, `MIN_HOLDING_HOURS` (72, anti-churn), `SELL_CONFIDENCE_GATE` (0.6, anti-churn), `WEIGHT_SENSITIVITY` (50 — weight amplification; negative avg ⇒ strategy disabled), `TARGET_POSITION_VOL` (0.30 — vol-aware sizing), `DRAWDOWN_BRAKE_PCT` (0.15) + `DRAWDOWN_SIZE_FACTOR` (0.5 — portfolio drawdown brake), `EXTENDED_HOURS_SIZE_FACTOR`, `EXTENDED_HOURS_MIN_CONFIDENCE`
- `BACKTEST_WEIGHTS_PATH` (default `data/backtest_weights.json`) — cold-start weight seeds produced by the backtest; consumed by `StrategyAllocator.get_weights`
- ATR sizing + cross-agent cap: `ATR_SIZING_ENABLED` (true), `ATR_STOP_MULTIPLE` (2.0), `POSITION_RISK_PCT` (0.01), `MAX_SECTOR_EXPOSURE_PCT` (0.40)

**Macro / market-timing gate (Phase 2 — strictly zero paid-API cost):**
- `MACRO_GATE_ENABLED` — hard on/off gate, **default false**; when false the Orchestrator uses the legacy SPY/VIX regime dampener
- `FRED_API_KEY` — free FRED key for yield curve + Fed net liquidity (empty → those inputs fail open)
- `SENTIMENT_LOCAL_ENABLED` (true) — local NLP overlay (VADER always; FinBERT only if `transformers`+`torch` installed; never a paid LLM API)
- `MACRO_HYSTERESIS_CYCLES` (3), `MACRO_BLACKOUT_HOURS` (12), `MACRO_BLACKOUT_SIZE_MULT` (0.5)
- Per-agent circuit breaker: `AGENT_BREAKER_DD_PCT` (0.20), `AGENT_BREAKER_FREEZE_DAYS` (5)

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
