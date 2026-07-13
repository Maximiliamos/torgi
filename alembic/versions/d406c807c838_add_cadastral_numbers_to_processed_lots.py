"""add cadastral_numbers to processed_lots

Revision ID: d406c807c838
Revises: 3c3e26449556
Create Date: 2026-05-07 00:47:23.177491

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd406c807c838'
down_revision: Union[str, Sequence[str], None] = '3c3e26449556'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
