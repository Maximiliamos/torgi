"""Add persistent cache for validated bulk geocoding results.

Revision ID: 09a1b2c3d4e5
Revises: f7a8b9c0d1e2
"""

from alembic import op
import sqlalchemy as sa


revision = "09a1b2c3d4e5"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "geo_query_cache",
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("cache_key"),
    )
    op.create_index("ix_geo_query_cache_provider", "geo_query_cache", ["provider"])
    op.create_index("ix_geo_query_cache_expires_at", "geo_query_cache", ["expires_at"])


def downgrade() -> None:
    op.drop_table("geo_query_cache")
