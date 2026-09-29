"""Add durable source progress timestamp for nationwide stall detection.

Revision ID: 2bc3d4e5f6a7
Revises: 1ab2c3d4e5f6
"""

from alembic import op
import sqlalchemy as sa


revision = "2bc3d4e5f6a7"
down_revision = "1ab2c3d4e5f6"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if "progress_at" not in _columns("lot_sync_source_runs"):
        op.add_column("lot_sync_source_runs", sa.Column("progress_at", sa.DateTime(), nullable=True))
    if "ix_lot_sync_source_runs_progress_at" not in _indexes("lot_sync_source_runs"):
        op.create_index(
            "ix_lot_sync_source_runs_progress_at",
            "lot_sync_source_runs",
            ["progress_at"],
        )


def downgrade() -> None:
    if "ix_lot_sync_source_runs_progress_at" in _indexes("lot_sync_source_runs"):
        op.drop_index("ix_lot_sync_source_runs_progress_at", table_name="lot_sync_source_runs")
    if "progress_at" in _columns("lot_sync_source_runs"):
        op.drop_column("lot_sync_source_runs", "progress_at")
