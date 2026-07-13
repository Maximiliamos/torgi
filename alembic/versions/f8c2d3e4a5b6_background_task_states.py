"""Add durable background task progress.

Revision ID: f8c2d3e4a5b6
Revises: e7b1c2d3a4f5
"""
from alembic import op
import sqlalchemy as sa

revision = "f8c2d3e4a5b6"
down_revision = "e7b1c2d3a4f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "background_task_states" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "background_task_states",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(length=100), nullable=False),
        sa.Column("task_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("progress_json", sa.JSON(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id"),
    )
    op.create_index("ix_background_task_states_task_id", "background_task_states", ["task_id"], unique=True)
    op.create_index("ix_background_task_states_task_type", "background_task_states", ["task_type"])
    op.create_index("ix_background_task_states_status", "background_task_states", ["status"])


def downgrade() -> None:
    if "background_task_states" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("background_task_states")
