# MarketMind-Pro — AI Governance Constitution

## Project Overview
MarketMind-Pro is a professional-grade autonomous trading intelligence system for TASE (Tel Aviv Stock Exchange) and US markets. Built and optimized for Apple Silicon M4 (arm64).

## Architecture Philosophy
- **Agents First**: Every major capability is an independent agent with a single responsibility.
- **Fail Loud**: Never silence exceptions. Log everything. Crash with context.
- **Security by Default**: No secrets in code. All credentials via `.env`. No hardcoded API keys.
- **Data Integrity**: Every DB mutation goes through SQLAlchemy models + Alembic migrations. No raw SQL.

---

## Folder Layout
```
MarketMind-Pro/
├── src/
│   ├── agents/           # Autonomous agent implementations
│   │   ├── news_search_agent.py    # Google Search MCP-powered news scanner
│   │   ├── quant_engine.py         # Technical analysis + signals engine
│   │   └── telegram_dispatcher.py  # Telegram bot + inline keyboards
│   ├── quant/            # Financial math (indicators, fibonacci, arbitrage)
│   │   ├── indicators.py
│   │   ├── fibonacci.py
│   │   └── arbitrage.py
│   ├── database/         # SQLAlchemy models, Alembic migrations, Redis cache
│   │   ├── models.py
│   │   ├── session.py
│   │   └── cache.py
│   ├── mcp/              # MCP server implementations
│   │   ├── google_search_mcp.py
│   │   └── sql_mcp_server.py
│   ├── ui/               # Chart generation (Plotly, GitHub Pages export)
│   │   └── charts.py
│   └── utils/            # Shared utilities
│       ├── config.py
│       ├── logger.py
│       └── timezone_utils.py
├── tests/
│   ├── unit/             # Pure logic tests (no I/O)
│   └── integration/      # DB, API, Redis tests
├── docs/                 # Architecture docs, runbooks
├── data/
│   ├── raw/              # Downloaded market data
│   ├── processed/        # Transformed/normalized data
│   └── cache/            # Local file cache (fallback)
├── scripts/              # One-off ops scripts (migrations, seeding)
├── alembic/              # Alembic migration environment
│   └── versions/
├── .github/
│   └── workflows/        # CI/CD pipelines
├── .claude/
│   └── hooks/            # Claude Code hooks
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── requirements.txt
├── .env.example
└── CLAUDE.md
```

---

## Coding Standards

### Python Style
- **Formatter**: Black (line-length = 88)
- **Linter**: Ruff (extends Flake8 ruleset + isort)
- **Type Hints**: Required on all public functions and class attributes
- **Docstrings**: Google-style for all public modules, classes, functions

### Naming Conventions
| Construct | Convention | Example |
|-----------|-----------|---------|
| Module | `snake_case` | `quant_engine.py` |
| Class | `PascalCase` | `QuantEngine` |
| Function | `snake_case` | `calculate_rsi()` |
| Constant | `UPPER_SNAKE` | `MAX_CACHE_TTL` |
| Env var | `UPPER_SNAKE` | `TELEGRAM_TOKEN` |

### Architecture Rules
1. Agents are **stateless** — state lives in PostgreSQL or Redis, never in memory.
2. All async I/O uses `asyncio`. No `time.sleep()` in agent loops — use `asyncio.sleep()`.
3. Database sessions are **never** shared between threads/coroutines.
4. Every agent has a `health_check()` method returning `{"status": "ok"|"error", "detail": str}`.
5. Financial calculations must have **unit tests with known expected outputs**.

---

## Security Policy
- `.env` is **NEVER** committed. The `.env.example` template is committed instead.
- Bandit must pass with zero HIGH-severity findings before any commit.
- No `eval()`, `exec()`, or `subprocess.shell=True` with user input.
- SQL queries use SQLAlchemy ORM or parameterized text() — never f-string SQL.
- API rate limits are respected via exponential backoff with jitter.

---

## Agent Contracts

### News-Search-Agent
- **Input**: Ticker symbol (e.g., `TEVA`, `AAPL`)
- **Output**: `SentimentReport` dataclass with score (-1.0 to 1.0), sources, Hebrew/English headlines
- **Tools**: Google Search MCP, web scraping (Globes, Bizportal, CNBC, Reuters)
- **Schedule**: Every 15 minutes during market hours

### Quant-Engine
- **Input**: Ticker + timeframe
- **Output**: `QuantSignal` dataclass with technical indicators, Fibonacci levels, arbitrage gap
- **Tools**: yfinance, pandas, numpy, PostgreSQL price history
- **Schedule**: Every 1 minute (cached in Redis for 60s)

### Telegram-Dispatcher
- **Input**: Telegram updates OR scheduled triggers
- **Output**: Formatted messages with inline keyboards to Telegram
- **Tools**: python-telegram-bot v22+, PostgreSQL alerts
- **Commands**: `/analyze [ticker]`, `/health`, `/fibonacci [ticker]`, `/arbitrage [ticker]`

---

## Git Hooks (pre-commit)
Located in `.git/hooks/pre-commit` (auto-installed via `scripts/install_hooks.sh`):
1. `ruff check src/ tests/` — fail on any lint error
2. `black --check src/ tests/` — fail if formatting needed
3. `bandit -r src/ -ll` — fail on MEDIUM+ severity security issues
4. `pytest tests/unit/ -x -q` — fail if any unit test fails

---

## Environment Variables
All defined in `.env.example`. Required for startup:
- `DATABASE_URL` — PostgreSQL connection string
- `REDIS_URL` — Redis connection string
- `TELEGRAM_TOKEN` — Bot API token (from @BotFather)
- `TELEGRAM_CHAT_ID` — Target chat/channel ID
- `GITHUB_PAGES_REPO` — For chart publishing
- `ALPHA_VANTAGE_KEY` — Optional: backup data source
- `EXCHANGERATE_API_KEY` — USD/ILS live rate

---

## MCP Server Contracts

### Google Search MCP
- **Purpose**: Real-time web context for news sentiment
- **Endpoint**: `localhost:8001`
- **Tools exposed**: `search_web(query, num_results)`, `scrape_page(url)`

### SQL MCP Server
- **Purpose**: AI-accessible structured data queries
- **Endpoint**: `localhost:8002`
- **Tools exposed**: `query_prices(ticker, from_date, to_date)`, `get_alerts()`, `get_arbitrage_history()`

---

## Testing Requirements
- Minimum 80% coverage on `src/quant/`
- All mathematical functions (RSI, MACD, Fibonacci) must have tests with known inputs/outputs
- Integration tests require Docker services (postgres, redis) — use `pytest -m integration`
- Mark slow tests with `@pytest.mark.slow`
