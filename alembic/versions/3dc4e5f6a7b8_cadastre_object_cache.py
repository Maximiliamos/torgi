"""Add persistent cadastral object cache.

Revision ID: 3dc4e5f6a7b8
Revises: 2bc3d4e5f6a7
"""

from alembic import op
import sqlalchemy as sa


revision = "3dc4e5f6a7b8"
down_revision = "2bc3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cadastre_object_cache",
        sa.Column("cadastral_number", sa.String(length=50), nullable=False),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("address_normalized", sa.Text(), nullable=True),
        sa.Column("object_type", sa.String(length=100), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("attributes_json", sa.JSON(), nullable=False),
        sa.Column("geometry_json", sa.JSON(), nullable=True),
        sa.Column("centroid_lat", sa.Float(), nullable=True),
        sa.Column("centroid_lon", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("is_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("cadastral_number"),
    )
    op.create_index("ix_cadastre_object_cache_address_normalized", "cadastre_object_cache", ["address_normalized"])
    op.create_index("ix_cadastre_object_cache_object_type", "cadastre_object_cache", ["object_type"])
    op.create_index("ix_cadastre_object_cache_centroid_lat", "cadastre_object_cache", ["centroid_lat"])
    op.create_index("ix_cadastre_object_cache_centroid_lon", "cadastre_object_cache", ["centroid_lon"])
    op.create_index("ix_cadastre_object_cache_source", "cadastre_object_cache", ["source"])
    op.create_index("ix_cadastre_object_cache_is_complete", "cadastre_object_cache", ["is_complete"])
    op.create_index("ix_cadastre_object_cache_fetched_at", "cadastre_object_cache", ["fetched_at"])
    op.create_index("ix_cadastre_object_cache_expires_at", "cadastre_object_cache", ["expires_at"])


def downgrade() -> None:
    op.drop_table("cadastre_object_cache")
