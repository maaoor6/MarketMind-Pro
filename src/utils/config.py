"""Central configuration using pydantic-settings. Reads from .env file."""

from functools import lru_cache
from typing import Annotated

from pydantic import BeforeValidator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_user_ids(v: object) -> list[int]:
    if v is None or v == "":
        return []
    if isinstance(v, int):
        return [v]
    if isinstance(v, list):
        return [int(x) for x in v]
    return [int(x.strip()) for x in str(v).split(",") if x.strip()]


def _parse_tickers(v: object) -> list[str]:
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return [str(x).strip().upper() for x in v if str(x).strip()]
    return [x.strip().upper() for x in str(v).split(",") if x.strip()]


_DEFAULT_TRADING_WATCHLIST = [
    "AAPL",
    "MSFT",
    "NVDA",
    "GOOGL",
    "META",
    "SPY",
    "QQQ",
    "TSLA",
    "AMZN",
    "AMD",
    "NFLX",
    "JPM",
    "XOM",
    "UNH",
    "COST",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_ignore_empty=True,  # treat ALLOWED_USER_IDS= (empty) as unset → use default []
    )

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://marketmind:password@localhost:5432/marketmind"
    )
    database_url_sync: str = Field(
        default="postgresql+psycopg2://marketmind:password@localhost:5432/marketmind"
    )

    # Cache
    redis_url: str = Field(default="redis://localhost:6379/0")

    # Telegram
    telegram_token: str = Field(default="")
    telegram_chat_id: str = Field(default="")
    # Comma-separated Telegram user IDs allowed to use the bot.
    # Example: ALLOWED_USER_IDS=123456789,987654321
    # Leave empty to allow ALL users (open mode — not recommended for production).
    allowed_user_ids: Annotated[list[int], BeforeValidator(_parse_user_ids)] = Field(
        default_factory=list
    )

    # External APIs
    alpha_vantage_key: str = Field(default="")
    exchangerate_api_key: str = Field(default="")

    # Market-data providers — comma-separated priority list used by the
    # provider abstraction (src/data). Callers get a composite that tries each
    # in order and fails over. Phase 1 adds edgar/stooq/finnhub/alphavantage.
    data_provider_priority: str = Field(default="yfinance")
    # Data-quality gate (src/data/validation.py). When ≥2 providers return a
    # price, flag disagreement beyond this fraction (0.02 = 2%). Cross-checks
    # are fail-open: a single source is always accepted.
    data_consensus_tolerance_pct: float = Field(default=0.02)
    # A live quote older than this many seconds is flagged stale.
    data_quality_max_age_seconds: float = Field(default=3600.0)
    # Comma-separated providers used as the independent cross-check source for
    # the OHLCV close (empty → cross-check disabled, single-source fail-open).
    data_crosscheck_providers: str = Field(default="")
    # SEC EDGAR requires a descriptive User-Agent with a contact (fair-access
    # rule). Free, no key. Override with your own contact for production use.
    sec_edgar_user_agent: str = Field(
        default="MarketMind-Pro/1.0 (research; contact via admin)"
    )
    # Free Finnhub API key (https://finnhub.io) — quote / company-news /
    # earnings-calendar. Empty → the Finnhub provider fails open (skipped).
    finnhub_api_key: str = Field(default="")

    # GitHub
    github_pages_repo: str = Field(default="maaoor6/MarketMind-Pro")
    github_token: str = Field(default="")

    # Google Search MCP
    google_search_mcp_port: int = Field(default=8001)
    sql_mcp_port: int = Field(default=8002)
    google_api_key: str = Field(default="")
    google_search_engine_id: str = Field(default="")

    # App
    app_env: str = Field(default="development")
    log_level: str = Field(default="INFO")
    timezone_us: str = Field(default="America/New_York")
    timezone_tase: str = Field(default="Asia/Jerusalem")

    # Cache TTLs (seconds)
    quote_cache_ttl: int = Field(default=60)
    news_cache_ttl: int = Field(default=900)  # 15 minutes

    # StockArena paper trading (simulated money)
    stock_arena_token: str = Field(default="")  # secret — env only, never logged
    stock_arena_base_url: str = Field(default="https://chaiware.org/stock_arena")
    stock_arena_bot_id: int = Field(default=2)
    stock_arena_user_id: int = Field(default=2)

    # Trading agent
    trading_enabled: bool = Field(default=False)  # master kill-switch
    trading_extended_hours: bool = Field(default=True)
    trading_interval_seconds: int = Field(default=300)
    # Fallback list only — used when the dynamic universe scan fails or
    # TRADING_DYNAMIC_UNIVERSE=false.
    trading_watchlist: Annotated[list[str], BeforeValidator(_parse_tickers)] = Field(
        default_factory=lambda: list(_DEFAULT_TRADING_WATCHLIST)
    )

    # Dynamic universe — the agent picks its own stocks via market screeners
    trading_dynamic_universe: bool = Field(default=True)
    # Share of the non-pinned universe reserved for the stable watchlist
    # (the rest comes from the daily screeners) — stops the agent from
    # trading only the day's most-extreme movers.
    universe_quality_ratio: float = Field(default=0.5)
    trading_universe_size: int = Field(default=20)  # max tickers per cycle
    trading_min_price: float = Field(default=5.0)  # no penny stocks
    trading_min_avg_volume: int = Field(default=1_000_000)  # 3-month avg daily
    trading_min_market_cap: float = Field(default=2_000_000_000.0)  # $2B+

    # Trading risk thresholds
    max_position_pct: float = Field(default=0.20)  # max 20% of portfolio per ticker
    max_open_positions: int = Field(default=5)
    min_trade_notional: float = Field(default=500.0)  # skip dust orders
    trade_commission: float = Field(default=2.5)  # $ per buy/sell order
    max_commission_pct: float = Field(default=0.01)  # round-trip fee ≤1% of notional
    cash_reserve_pct: float = Field(default=0.05)  # never spend last 5% of cash
    stop_loss_pct: float = Field(default=0.05)  # exit at -5% from avg price
    take_profit_pct: float = Field(default=0.10)  # hard cap at +10%
    trail_stop_pct: float = Field(default=0.04)  # trailing stop below high-water mark
    max_daily_loss_pct: float = Field(default=0.03)  # daily circuit breaker
    ticker_cooldown_minutes: int = Field(default=30)
    min_confidence: float = Field(default=0.55)
    # Anti-churn: strategy SELLs (not protective exits) are ignored while a
    # position is younger than this, or when the sell confidence is below the
    # gate — stops one strategy dumping another's day-old entry.
    min_holding_hours: int = Field(default=72)
    sell_confidence_gate: float = Field(default=0.6)
    # Learning: how strongly per-signal avg returns separate strategy weights
    # (1 + sensitivity × avg). Negative averages disable a strategy entirely.
    weight_sensitivity: float = Field(default=50.0)
    # Volatility-aware sizing: shrink positions whose annualized 20d vol
    # exceeds this target (budget × target/vol). No vol data → full size.
    target_position_vol: float = Field(default=0.30)
    # Drawdown brake: once the portfolio falls this far below its all-time
    # high-water mark, new-buy budgets are multiplied by the size factor
    # until the portfolio recovers (protective exits unaffected).
    drawdown_brake_pct: float = Field(default=0.15)
    drawdown_size_factor: float = Field(default=0.5)
    extended_hours_size_factor: float = Field(default=0.5)
    extended_hours_min_confidence: float = Field(default=0.7)

    # Backtest weight seeds — cold-start priors for the StrategyAllocator,
    # produced by `python -m src.backtest --export-weights` (local file).
    backtest_weights_path: str = Field(default="data/backtest_weights.json")

    # ── Macro / market-timing gate (Phase 2) ──────────────────────────
    # Hard on/off gate that decides whether it is a good time to trade at
    # all, from quantitative macro data (SPY/QQQ trend, VIX + term structure,
    # credit/rates, Fed liquidity, cross-asset), an event blackout calendar,
    # and a strictly-local zero-cost sentiment overlay. Disabled by default
    # (same fail-safe posture as trading_enabled).
    macro_gate_enabled: bool = Field(default=False)
    # Free FRED API key (https://fred.stlouisfed.org) for credit/rates and Fed
    # net-liquidity series. Empty → those blocks fail open (neutral).
    fred_api_key: str = Field(default="")
    # Local NLP sentiment overlay (VADER always; FinBERT only if transformers +
    # torch are installed). Never calls a paid LLM API.
    sentiment_local_enabled: bool = Field(default=True)
    # Anti-whipsaw hysteresis: the gate flips OFF immediately on risk but
    # returns ON only after this many consecutive stable cycles.
    macro_hysteresis_cycles: int = Field(default=3)
    # Reduce/halt new entries within this many hours around a high-impact
    # macro event (FOMC/CPI/NFP).
    macro_blackout_hours: float = Field(default=12.0)
    # Buy-budget multiplier applied while inside a blackout window (0 = halt).
    macro_blackout_size_mult: float = Field(default=0.5)

    # ── Per-agent circuit breaker (Phase 4) ───────────────────────────
    # Freeze a single thematic agent for N days if its realized drawdown over
    # the rolling window breaches the threshold — the rest keep trading.
    agent_breaker_dd_pct: float = Field(default=0.20)
    agent_breaker_freeze_days: int = Field(default=5)

    # ── ATR dollar-at-risk sizing (Phase 3) ───────────────────────────
    # When enabled, a position's notional is capped so that an adverse move of
    # atr_stop_multiple × ATR risks at most position_risk_pct of the portfolio —
    # equalizing dollar-at-risk across positions (volatile names get fewer $).
    # Fail-open: no ATR data → the existing vol-aware sizing stands.
    atr_sizing_enabled: bool = Field(default=True)
    atr_stop_multiple: float = Field(default=2.0)  # stop = N × ATR below entry
    position_risk_pct: float = Field(default=0.01)  # ≤1% of portfolio at risk

    # ── Cross-agent correlation / exposure cap (Phase 3) ──────────────
    # Combined portfolio exposure to any one GICS sector is capped here so
    # multiple agents don't pile into the same beta. Fail-open when a ticker's
    # sector is unknown.
    max_sector_exposure_pct: float = Field(default=0.40)

    # ── Infrastructure agents (Phase 2A) ──────────────────────────────
    # Non-strategy cycle observers (data-validation, sentiment, risk-overseer)
    # that can only TIGHTEN the buy path (block/scale buys, shrink the budget).
    # Fail-open; enabling them changes nothing unless they actually find a
    # problem. The live agent is still gated by trading_enabled.
    infra_agents_enabled: bool = Field(default=True)
    # Cached sentiment at/below veto blocks a buy; at/below caution halves it.
    sentiment_veto_score: float = Field(default=-0.5)
    sentiment_scale_score: float = Field(default=-0.2)
    # RiskOverseer (Phase 2A.5) — institutional factor/correlation caps.
    # A new entry correlated above this to any current holding is blocked.
    correlation_max: float = Field(default=0.70)
    # Portfolio-weighted net beta vs SPY is capped here (confirmed target).
    portfolio_beta_max: float = Field(default=1.20)
    # Portfolio-wide single-sector cluster cap (tighter than the per-agent one).
    sector_cluster_max: float = Field(default=0.30)
    # Rolling lookback (trading days) for the correlation / beta estimates.
    risk_corr_lookback_days: int = Field(default=60)

    # ── Shadow / paper pipeline (Phase 2B.4) ──────────────────────────
    # A walk-forward winner must run this many days in read-only shadow mode
    # (signals recorded + scored, never executed) with no concept drift before
    # it can be promoted into default_strategies().
    shadow_mode_days: int = Field(default=30)
    # Comma-separated experimental strategy names currently in shadow mode.
    shadow_strategy_names: str = Field(default="")


@lru_cache
def get_settings() -> Settings:
    """Return cached settings singleton."""
    return Settings()


settings = get_settings()
