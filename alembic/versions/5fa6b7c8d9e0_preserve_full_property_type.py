"""Preserve official property classifier labels without truncation.

Revision ID: 5fa6b7c8d9e0
Revises: 4ef5a6b7c8d9
"""
from alembic import op
import sqlalchemy as sa

revision = "5fa6b7c8d9e0"
down_revision = "4ef5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("processed_lots") as batch:
        batch.alter_column("property_type", existing_type=sa.String(50),
                           type_=sa.Text(), existing_nullable=True)


def downgrade() -> None:
    if op.get_bind().execute(sa.text(
        "SELECT 1 FROM processed_lots WHERE length(property_type) > 50 LIMIT 1"
    )).first() is not None:
        raise RuntimeError("Cannot narrow property_type without losing existing source labels")
    with op.batch_alter_table("processed_lots") as batch:
        batch.alter_column("property_type", existing_type=sa.Text(),
                           type_=sa.String(50), existing_nullable=True)
