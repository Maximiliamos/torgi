"""Add a coordinate index for viewport map queries.

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
"""

from alembic import op
import sqlalchemy as sa

revision = "a2b3c4d5e6f7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def _has_index(table: str, name: str) -> bool:
    return any(item["name"] == name for item in sa.inspect(op.get_bind()).get_indexes(table))


def upgrade() -> None:
    if not _has_index("lot_geo_snapshots", "ix_lot_geo_snapshots_viewport"):
        op.create_index(
            "ix_lot_geo_snapshots_viewport",
            "lot_geo_snapshots",
            ["centroid_lat", "centroid_lon"],
        )


def downgrade() -> None:
    if _has_index("lot_geo_snapshots", "ix_lot_geo_snapshots_viewport"):
        op.drop_index("ix_lot_geo_snapshots_viewport", table_name="lot_geo_snapshots")
