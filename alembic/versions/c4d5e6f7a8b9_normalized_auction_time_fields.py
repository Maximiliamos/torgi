"""Add normalized auction application start and timezone.

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c4d5e6f7a8b9"
down_revision: str | None = "b3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("source_lots", sa.Column("application_start_at", sa.DateTime(), nullable=True))
    op.add_column("source_lots", sa.Column("auction_timezone", sa.String(length=64), nullable=True))
    op.create_index("ix_source_lots_application_start_at", "source_lots", ["application_start_at"])


def downgrade() -> None:
    op.drop_index("ix_source_lots_application_start_at", table_name="source_lots")
    op.drop_column("source_lots", "auction_timezone")
    op.drop_column("source_lots", "application_start_at")
