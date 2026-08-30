"""Track the geocoding input used by the latest attempt.

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d5e6f7a8b9c0"
down_revision: str | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("processed_lots", sa.Column("geo_input_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("processed_lots", "geo_input_hash")
