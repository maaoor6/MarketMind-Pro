"""Initial schema — all MarketMind-Pro tables.

Revision ID: 0001
Revises:
Create Date: 2026-04-05 00:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "price_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("exchange", sa.String(10), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timeframe", sa.String(5), nullable=False),
        sa.Column("open", sa.Numeric(18, 6), nullable=False),
        sa.Column("high", sa.Numeric(18, 6), nullable=False),
        sa.Column("low", sa.Numeric(18, 6), nullable=False),
        sa.Column("close", sa.Numeric(18, 6), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column("adj_close", sa.Numeric(18, 6), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_price_history_ticker_ts", "price_history", ["ticker", "timestamp"])
    op.create_index(op.f("ix_price_history_ticker"), "price_history", ["ticker"])

    op.create_table(
        "dual_listing_gaps",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticker_us", sa.String(20), nullable=False),
        sa.Column("ticker_tase", sa.String(20), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price_us_usd", sa.Numeric(18, 6), nullable=False),
        sa.Column("price_tase_ils", sa.Numeric(18, 6), nullable=False),
        sa.Column("usd_ils_rate", sa.Numeric(10, 6), nullable=False),
        sa.Column("price_tase_in_usd", sa.Numeric(18, 6), nullable=False),
        sa.Column("gap_pct", sa.Numeric(8, 4), nullable=False),
        sa.Column("gap_direction", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "user_alerts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.String(50), nullable=False),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("alert_type", sa.String(30), nullable=False),
        sa.Column("threshold", sa.Numeric(18, 6), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_user_alerts_chat_id"), "user_alerts", ["chat_id"])

    op.create_table(
        "insider_transactions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("insider_name", sa.String(200), nullable=False),
        sa.Column("insider_title", sa.String(200), nullable=True),
        sa.Column("transaction_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("transaction_type", sa.String(20), nullable=False),
        sa.Column("shares", sa.BigInteger(), nullable=False),
        sa.Column("price_per_share", sa.Numeric(18, 6), nullable=True),
        sa.Column("total_value", sa.Numeric(20, 2), nullable=True),
        sa.Column("filing_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_insider_transactions_ticker"), "insider_transactions", ["ticker"])

    op.create_table(
        "sentiment_records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("score", sa.Numeric(5, 4), nullable=False),
        sa.Column("headline_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sources", sa.Text(), nullable=True),
        sa.Column("summary_he", sa.Text(), nullable=True),
        sa.Column("summary_en", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sentiment_records_ticker"), "sentiment_records", ["ticker"])


def downgrade() -> None:
    op.drop_table("sentiment_records")
    op.drop_table("insider_transactions")
    op.drop_table("user_alerts")
    op.drop_table("dual_listing_gaps")
    op.drop_index("ix_price_history_ticker_ts", table_name="price_history")
    op.drop_table("price_history")
