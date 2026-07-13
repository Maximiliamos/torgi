"""Add AI valuation provenance and execution metadata.

Revision ID: a9d3e4f5b6c7
Revises: f8c2d3e4a5b6
"""
from alembic import op
import sqlalchemy as sa

revision = "a9d3e4f5b6c7"
down_revision = "f8c2d3e4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("valuation_runs")}
    with op.batch_alter_table("valuation_runs") as batch:
        if "provider" not in columns:
            batch.add_column(sa.Column("provider", sa.String(50), nullable=False, server_default="legacy"))
        if "model" not in columns:
            batch.add_column(sa.Column("model", sa.String(200), nullable=True))
        if "prompt_version" not in columns:
            batch.add_column(sa.Column("prompt_version", sa.String(50), nullable=False, server_default="v1"))
        if "duration_ms" not in columns:
            batch.add_column(sa.Column("duration_ms", sa.Integer(), nullable=True))
        if "attempt_count" not in columns:
            batch.add_column(sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1"))
        if "status" not in columns:
            batch.add_column(sa.Column("status", sa.String(20), nullable=False, server_default="completed"))
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("valuation_runs")}
    if "ix_valuation_runs_provider" not in indexes:
        op.create_index("ix_valuation_runs_provider", "valuation_runs", ["provider"])
    if "ix_valuation_runs_status" not in indexes:
        op.create_index("ix_valuation_runs_status", "valuation_runs", ["status"])


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("valuation_runs")}
    with op.batch_alter_table("valuation_runs") as batch:
        for name in ("status", "attempt_count", "duration_ms", "prompt_version", "model", "provider"):
            if name in columns:
                batch.drop_column(name)
