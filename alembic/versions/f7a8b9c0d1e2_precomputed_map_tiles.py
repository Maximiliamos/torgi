"""Add versioned precomputed map datasets and tiles.

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
"""
from alembic import op
import sqlalchemy as sa

revision = "f7a8b9c0d1e2"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())
    if "map_datasets" not in tables:
        op.create_table(
            "map_datasets",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("version", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("is_current", sa.Boolean(), nullable=False),
            sa.Column("point_count", sa.Integer(), nullable=False),
            sa.Column("tile_count", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("published_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("version"),
        )
    if "map_tiles" not in tables:
        op.create_table(
            "map_tiles",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("dataset_id", sa.Integer(), nullable=False),
            sa.Column("z", sa.Integer(), nullable=False),
            sa.Column("x", sa.Integer(), nullable=False),
            sa.Column("y", sa.Integer(), nullable=False),
            sa.Column("feature_count", sa.Integer(), nullable=False),
            sa.Column("etag", sa.String(length=64), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.ForeignKeyConstraint(["dataset_id"], ["map_datasets.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("dataset_id", "z", "x", "y", name="uq_map_tile_coordinate"),
        )

    # A pre-release create_all deployment may already contain these tables.
    # Deterministically retain the newest current row before enforcing the invariant.
    connection.execute(sa.text("""
        UPDATE map_datasets
        SET is_current = false
        WHERE is_current = true
          AND id <> (
            SELECT id FROM map_datasets
            WHERE is_current = true
            ORDER BY (published_at IS NOT NULL) DESC, published_at DESC, created_at DESC, id DESC
            LIMIT 1
          )
    """))
    inspector = sa.inspect(connection)
    dataset_indexes = {item["name"] for item in inspector.get_indexes("map_datasets")}
    tile_indexes = {item["name"] for item in inspector.get_indexes("map_tiles")}
    if "ix_map_datasets_status" not in dataset_indexes:
        op.create_index("ix_map_datasets_status", "map_datasets", ["status"])
    if "ix_map_datasets_is_current" not in dataset_indexes:
        op.create_index("ix_map_datasets_is_current", "map_datasets", ["is_current"])
    if "uq_map_datasets_single_current" not in dataset_indexes:
        op.create_index(
            "uq_map_datasets_single_current",
            "map_datasets",
            [sa.literal_column("(1)")],
            unique=True,
            postgresql_where=sa.text("is_current"),
            sqlite_where=sa.text("is_current = 1"),
        )
    if "ix_map_tiles_dataset_id" not in tile_indexes:
        op.create_index("ix_map_tiles_dataset_id", "map_tiles", ["dataset_id"])


def downgrade() -> None:
    op.drop_table("map_tiles")
    op.drop_table("map_datasets")
