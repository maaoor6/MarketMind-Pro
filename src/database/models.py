"""SQLAlchemy ORM models for MarketMind-Pro."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class PriceHistory(Base):
    """OHLCV price history for any ticker."""

    __tablename__ = "price_history"
    __table_args__ = (Index("ix_price_history_ticker_ts", "ticker", "timestamp"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    exchange: Mapped[str] = mapped_column(
        String(10), nullable=False
    )  # NYSE, TASE, etc.
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(5), nullable=False)  # 1m, 5m, 1d

    open: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    adj_close: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<PriceHistory {self.ticker} {self.timestamp} close={self.close}>"


class DualListingGap(Base):
    """Arbitrage gap tracking for dual-listed stocks (TASE + NYSE)."""

    __tablename__ = "dual_listing_gaps"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker_us: Mapped[str] = mapped_column(String(20), nullable=False)
    ticker_tase: Mapped[str] = mapped_column(String(20), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    price_us_usd: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    price_tase_ils: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    usd_ils_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    price_tase_in_usd: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    gap_pct: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    gap_direction: Mapped[str] = mapped_column(
        String(10), nullable=False
    )  # US_PREMIUM / TASE_PREMIUM

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class UserAlert(Base):
    """Price and signal alerts for Telegram notifications."""

    __tablename__ = "user_alerts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    alert_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # PRICE_ABOVE, PRICE_BELOW, RSI_OVERSOLD, VOLUME_SPIKE, FIBONACCI
    threshold: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<UserAlert {self.ticker} {self.alert_type} threshold={self.threshold}>"


class InsiderTransaction(Base):
    """SEC Form 4 insider transactions."""

    __tablename__ = "insider_transactions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    insider_name: Mapped[str] = mapped_column(String(200), nullable=False)
    insider_title: Mapped[str] = mapped_column(String(200), nullable=True)
    transaction_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    transaction_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # BUY, SELL
    shares: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price_per_share: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=True)
    total_value: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=True)
    filing_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TradeRecord(Base):
    """Executed paper trades placed by the autonomous trading agent."""

    __tablename__ = "trade_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(4), nullable=False)  # BUY, SELL
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fill_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    notional: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    strategy: Mapped[str] = mapped_column(String(40), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    portfolio_value_after: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 6), nullable=True
    )
    cash_after: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<TradeRecord {self.action} {self.quantity} {self.ticker}>"


class StrategySignalRecord(Base):
    """Every non-HOLD strategy signal, scored later for virtual performance."""

    __tablename__ = "strategy_signals"
    __table_args__ = (Index("ix_strategy_signals_scored", "strategy", "scored"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    strategy: Mapped[str] = mapped_column(String(40), nullable=False)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    action: Mapped[str] = mapped_column(String(4), nullable=False)  # BUY, SELL
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    decision_price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    eval_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    scored: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    virtual_return_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 4), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class StrategyPerformance(Base):
    """Aggregate virtual performance per strategy (drives capital weights)."""

    __tablename__ = "strategy_performance"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    strategy: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    signals_scored: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    avg_return_pct: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), default=0, nullable=False
    )
    win_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=0, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), server_default=func.now()
    )


class SentimentRecord(Base):
    """News sentiment analysis results."""

    __tablename__ = "sentiment_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)  # -1.0 to 1.0
    headline_count: Mapped[int] = mapped_column(nullable=False, default=0)
    sources: Mapped[str] = mapped_column(Text, nullable=True)  # JSON array of sources
    summary_he: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # Hebrew summary
    summary_en: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # English summary
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
