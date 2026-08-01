"""application users for web authentication

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
"""

from alembic import op
import sqlalchemy as sa


revision = "e0f1a2b3c4d5"
down_revision = "d9e0f1a2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False, server_default="reader"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_login_at", sa.DateTime()),
        sa.UniqueConstraint("username", name="uq_app_users_username"),
    )
    op.create_index("ix_app_users_username", "app_users", ["username"])
    op.create_index("ix_app_users_role", "app_users", ["role"])
    op.create_index("ix_app_users_is_active", "app_users", ["is_active"])


def downgrade() -> None:
    op.drop_table("app_users")
