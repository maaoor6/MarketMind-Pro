"""Central configuration using pydantic-settings. Reads from .env file."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
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

    # External APIs
    alpha_vantage_key: str = Field(default="")
    exchangerate_api_key: str = Field(default="")

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


@lru_cache
def get_settings() -> Settings:
    """Return cached settings singleton."""
    return Settings()


settings = get_settings()
