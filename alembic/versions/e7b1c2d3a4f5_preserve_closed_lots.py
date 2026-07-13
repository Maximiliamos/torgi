"""Preserve closed lots and repair the published schema.

Revision ID: e7b1c2d3a4f5
Revises: d406c807c838
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7b1c2d3a4f5"
down_revision: Union[str, Sequence[str], None] = "d406c807c838"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    columns = _columns("processed_lots")
    with op.batch_alter_table("processed_lots") as batch:
        if "cadastral_numbers" not in columns:
            batch.add_column(sa.Column("cadastral_numbers", sa.JSON(), nullable=True))
        if "is_archived" not in columns:
            batch.add_column(sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()))
        if "archived_at" not in columns:
            batch.add_column(sa.Column("archived_at", sa.DateTime(), nullable=True))
        if "closed_at" not in columns:
            batch.add_column(sa.Column("closed_at", sa.DateTime(), nullable=True))

    inspector = sa.inspect(op.get_bind())
    index_names = {item["name"] for item in inspector.get_indexes("processed_lots")}
    if "ix_processed_lots_is_archived" not in index_names:
        op.create_index("ix_processed_lots_is_archived", "processed_lots", ["is_archived"])

    if "lot_status_history" not in tables:
        op.create_table(
            "lot_status_history",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("lot_id", sa.Integer(), nullable=False),
            sa.Column("old_status", sa.String(length=100), nullable=True),
            sa.Column("new_status", sa.String(length=100), nullable=False),
            sa.Column("changed_at", sa.DateTime(), nullable=False),
            sa.Column("source", sa.String(length=50), nullable=False),
            sa.ForeignKeyConstraint(["lot_id"], ["processed_lots.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_lot_status_history_lot_id", "lot_status_history", ["lot_id"])
        op.create_index("ix_lot_status_history_changed_at", "lot_status_history", ["changed_at"])

    op.execute(
        sa.text(
            "UPDATE processed_lots SET is_archived = :archived, "
            "archived_at = COALESCE(archived_at, last_update), "
            "closed_at = COALESCE(closed_at, last_update) "
            "WHERE auction_status = 'closed'"
        ).bindparams(archived=True)
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "lot_status_history" in inspector.get_table_names():
        op.drop_table("lot_status_history")
    columns = _columns("processed_lots")
    with op.batch_alter_table("processed_lots") as batch:
        for name in ("closed_at", "archived_at", "is_archived"):
            if name in columns:
                batch.drop_column(name)
    # cadastral_numbers may predate this corrective migration, so it is intentionally preserved.
