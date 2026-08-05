"""Optimize viewport map queries.

Revision ID: f1a2b3c4d5e6
Revises: e0f1a2b3c4d5
"""

from alembic import op
import sqlalchemy as sa

revision = "f1a2b3c4d5e6"
down_revision = "e0f1a2b3c4d5"
branch_labels = None
depends_on = None


def _has_index(table: str, name: str) -> bool:
    return any(item["name"] == name for item in sa.inspect(op.get_bind()).get_indexes(table))


def upgrade() -> None:
    if not _has_index("processed_lots", "ix_processed_lots_map_feed"):
        op.create_index(
            "ix_processed_lots_map_feed",
            "processed_lots",
            ["is_archived", "duplicate_of_id", "region_slug", "last_update"],
        )
    if not _has_index("lot_geo_snapshots", "ix_lot_geo_snapshots_latest"):
        op.create_index(
            "ix_lot_geo_snapshots_latest",
            "lot_geo_snapshots",
            ["lot_id", "id"],
        )


def downgrade() -> None:
    if _has_index("lot_geo_snapshots", "ix_lot_geo_snapshots_latest"):
        op.drop_index("ix_lot_geo_snapshots_latest", table_name="lot_geo_snapshots")
    if _has_index("processed_lots", "ix_processed_lots_map_feed"):
        op.drop_index("ix_processed_lots_map_feed", table_name="processed_lots")
