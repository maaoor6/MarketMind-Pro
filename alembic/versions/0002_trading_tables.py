"""Trading tables — trade_records, strategy_signals, strategy_performance.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-06 00:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trade_records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("action", sa.String(4), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("fill_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("notional", sa.Numeric(18, 6), nullable=True),
        sa.Column("strategy", sa.String(40), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("portfolio_value_after", sa.Numeric(18, 6), nullable=True),
        sa.Column("cash_after", sa.Numeric(18, 6), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_trade_records_ticker"), "trade_records", ["ticker"])

    op.create_table(
        "strategy_signals",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("strategy", sa.String(40), nullable=False),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("action", sa.String(4), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("decision_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("eval_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scored", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("virtual_return_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_strategy_signals_scored", "strategy_signals", ["strategy", "scored"]
    )

    op.create_table(
        "strategy_performance",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("strategy", sa.String(40), nullable=False),
        sa.Column("signals_scored", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("avg_return_pct", sa.Numeric(8, 4), nullable=False, server_default="0"),
        sa.Column("win_rate", sa.Numeric(5, 4), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("strategy"),
    )


def downgrade() -> None:
    op.drop_table("strategy_performance")
    op.drop_index("ix_strategy_signals_scored", table_name="strategy_signals")
    op.drop_table("strategy_signals")
    op.drop_index(op.f("ix_trade_records_ticker"), table_name="trade_records")
    op.drop_table("trade_records")
