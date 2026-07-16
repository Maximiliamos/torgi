"""Scope external identifiers by source.

Revision ID: b0c1d2e3f4a5
Revises: a9d3e4f5b6c7
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b0c1d2e3f4a5"
down_revision: Union[str, Sequence[str], None] = "a9d3e4f5b6c7"
branch_labels = None
depends_on = None


def _drop_external_id_index(table: str) -> None:
    inspector = sa.inspect(op.get_bind())
    for index in inspector.get_indexes(table):
        if index["name"] == f"ix_{table}_external_id":
            op.drop_index(index["name"], table_name=table)
            return


def upgrade() -> None:
    _drop_external_id_index("raw_lots")
    _drop_external_id_index("processed_lots")

    with op.batch_alter_table("raw_lots") as batch:
        batch.create_unique_constraint(
            "uq_raw_lots_source_external_id",
            ["source", "external_id"],
        )
    with op.batch_alter_table("processed_lots") as batch:
        batch.create_unique_constraint(
            "uq_processed_lots_source_system_external_id",
            ["source_system", "external_id"],
        )

    op.create_index("ix_raw_lots_external_id", "raw_lots", ["external_id"], unique=False)
    op.create_index("ix_processed_lots_external_id", "processed_lots", ["external_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_raw_lots_external_id", table_name="raw_lots")
    op.drop_index("ix_processed_lots_external_id", table_name="processed_lots")

    with op.batch_alter_table("raw_lots") as batch:
        batch.drop_constraint("uq_raw_lots_source_external_id", type_="unique")
    with op.batch_alter_table("processed_lots") as batch:
        batch.drop_constraint(
            "uq_processed_lots_source_system_external_id",
            type_="unique",
        )

    op.create_index("ix_raw_lots_external_id", "raw_lots", ["external_id"], unique=True)
    op.create_index("ix_processed_lots_external_id", "processed_lots", ["external_id"], unique=True)
