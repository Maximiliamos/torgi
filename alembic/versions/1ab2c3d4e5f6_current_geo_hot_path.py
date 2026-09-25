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
    if not _has_index("lot_geo_snapshots", "ix_lot_geo_snapshots_lot_observed_id"):
        op.create_index(
            "ix_lot_geo_snapshots_lot_observed_id",
            "lot_geo_snapshots",
            ["lot_id", "observed_at", "id"],
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
        sa.column("geo_input_hash", sa.String()),
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

    if bind.dialect.name == "postgresql":
        # One pass over snapshot history is materially cheaper than five
        # correlated latest-row lookups per processed lot, and shortens the
        # migration's row-lock window on production PostgreSQL.
        bind.execute(
            sa.text(
                """
                UPDATE processed_lots AS p
                SET current_geo_lat = latest.centroid_lat,
                    current_geo_lon = latest.centroid_lon,
                    current_geo_source = latest.geo_source,
                    current_geo_confidence = latest.geo_confidence,
                    current_geo_observed_at = latest.observed_at
                FROM (
                    SELECT DISTINCT ON (lot_id)
                        lot_id,
                        centroid_lat,
                        centroid_lon,
                        geo_source,
                        geo_confidence,
                        observed_at
                    FROM lot_geo_snapshots
                    ORDER BY lot_id, observed_at DESC, id DESC
                ) AS latest
                WHERE latest.lot_id = p.id
                  AND p.geo_input_hash IS NOT NULL
                """
            )
        )
    else:
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
            .where(
                has_snapshot,
                processed.c.geo_input_hash.is_not(None),
            )
            .values(
                current_geo_lat=latest(snapshots.c.centroid_lat),
                current_geo_lon=latest(snapshots.c.centroid_lon),
                current_geo_source=latest(snapshots.c.geo_source),
                current_geo_confidence=latest(snapshots.c.geo_confidence),
                current_geo_observed_at=latest(snapshots.c.observed_at),
            )
        )


def downgrade() -> None:
    if _has_index("lot_geo_snapshots", "ix_lot_geo_snapshots_lot_observed_id"):
        op.drop_index("ix_lot_geo_snapshots_lot_observed_id", table_name="lot_geo_snapshots")
    if _has_index("processed_lots", "ix_processed_lots_current_geo_viewport"):
        op.drop_index("ix_processed_lots_current_geo_viewport", table_name="processed_lots")
    op.drop_column("processed_lots", "current_geo_observed_at")
    op.drop_column("processed_lots", "current_geo_confidence")
    op.drop_column("processed_lots", "current_geo_source")
    op.drop_column("processed_lots", "current_geo_lon")
    op.drop_column("processed_lots", "current_geo_lat")
