"""Add simple user lot issue reports.

Revision ID: 4ef5a6b7c8d9
Revises: 3dc4e5f6a7b8
"""

from alembic import op
import sqlalchemy as sa


revision = "4ef5a6b7c8d9"
down_revision = "3dc4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lot_issue_reports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("lot_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["lot_id"], ["processed_lots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lot_issue_reports_lot_id", "lot_issue_reports", ["lot_id"])
    op.create_index("ix_lot_issue_reports_user_id", "lot_issue_reports", ["user_id"])
    op.create_index("ix_lot_issue_reports_created_at", "lot_issue_reports", ["created_at"])


def downgrade() -> None:
    op.drop_table("lot_issue_reports")
