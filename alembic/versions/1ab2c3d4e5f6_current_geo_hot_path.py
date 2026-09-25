"""Denormalize the latest GEO snapshot onto processed lots.

Revision ID: 1ab2c3d4e5f6
Revises: 09a1b2c3d4e5
"""

from alembic import op
import sqlalchemy as sa


revision = "1ab2c3d4e5f6"
down_revision = "09a1b2c3d4e5"
branch_labels = None
depends_on = None


def _has_index(table: str, name: str) -> bool:
    return any(item["name"] == name for item in sa.inspect(op.get_bind()).get_indexes(table))


def upgrade() -> None:
    op.add_column("processed_lots", sa.Column("current_geo_lat", sa.Float(), nullable=True))
    op.add_column("processed_lots", sa.Column("current_geo_lon", sa.Float(), nullable=True))
    op.add_column("processed_lots", sa.Column("current_geo_source", sa.String(length=50), nullable=True))
    op.add_column("processed_lots", sa.Column("current_geo_confidence", sa.String(length=20), nullable=True))
    op.add_column("processed_lots", sa.Column("current_geo_observed_at", sa.DateTime(), nullable=True))

    if not _has_index("processed_lots", "ix_processed_lots_current_geo_viewport"):
        op.create_index(
            "ix_processed_lots_current_geo_viewport",
            "processed_lots",
            ["current_geo_lat", "current_geo_lon"],
        )

    bind = op.get_bind()
    processed = sa.table(
        "processed_lots",
        sa.column("id", sa.Integer()),
        sa.column("current_geo_lat", sa.Float()),
        sa.column("current_geo_lon", sa.Float()),
        sa.column("current_geo_source", sa.String()),
        sa.column("current_geo_confidence", sa.String()),
        sa.column("current_geo_observed_at", sa.DateTime()),
    )
    snapshots = sa.table(
        "lot_geo_snapshots",
        sa.column("id", sa.Integer()),
        sa.column("lot_id", sa.Integer()),
        sa.column("geo_source", sa.String()),
        sa.column("geo_confidence", sa.String()),
        sa.column("centroid_lat", sa.Float()),
        sa.column("centroid_lon", sa.Float()),
        sa.column("observed_at", sa.DateTime()),
    )

    def latest(column):
        return (
            sa.select(column)
            .where(snapshots.c.lot_id == processed.c.id)
            .order_by(snapshots.c.observed_at.desc(), snapshots.c.id.desc())
            .limit(1)
            .scalar_subquery()
        )

    has_snapshot = sa.exists(
        sa.select(sa.literal(1)).where(snapshots.c.lot_id == processed.c.id)
    )
    bind.execute(
        sa.update(processed)
        .where(has_snapshot)
        .values(
            current_geo_lat=latest(snapshots.c.centroid_lat),
            current_geo_lon=latest(snapshots.c.centroid_lon),
            current_geo_source=latest(snapshots.c.geo_source),
            current_geo_confidence=latest(snapshots.c.geo_confidence),
            current_geo_observed_at=latest(snapshots.c.observed_at),
        )
    )


def downgrade() -> None:
    if _has_index("processed_lots", "ix_processed_lots_current_geo_viewport"):
        op.drop_index("ix_processed_lots_current_geo_viewport", table_name="processed_lots")
    op.drop_column("processed_lots", "current_geo_observed_at")
    op.drop_column("processed_lots", "current_geo_confidence")
    op.drop_column("processed_lots", "current_geo_source")
    op.drop_column("processed_lots", "current_geo_lon")
    op.drop_column("processed_lots", "current_geo_lat")
