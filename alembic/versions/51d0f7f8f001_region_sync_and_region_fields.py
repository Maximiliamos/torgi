"""region sync and region fields

Revision ID: 51d0f7f8f001
Revises: da29e0ae9131
Create Date: 2026-04-17 12:40:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "51d0f7f8f001"
down_revision: Union[str, Sequence[str], None] = "da29e0ae9131"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("processed_lots", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source_system", sa.String(length=50), nullable=False, server_default="tbankrot"))
        batch_op.add_column(sa.Column("region_slug", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("region_name", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("source_url", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("detail_level", sa.String(length=30), nullable=False, server_default="detail"))
        batch_op.create_index(batch_op.f("ix_processed_lots_source_system"), ["source_system"], unique=False)
        batch_op.create_index(batch_op.f("ix_processed_lots_region_slug"), ["region_slug"], unique=False)

    op.execute(
        """
        UPDATE processed_lots
        SET source_system = CASE
            WHEN source LIKE 'gorod-torgi:%' THEN 'gorod_torgi'
            WHEN source = 'tbankrot' THEN 'tbankrot'
            ELSE source
        END,
        region_slug = CASE
            WHEN source LIKE 'gorod-torgi:%' THEN substr(source, instr(source, ':') + 1)
            ELSE region_slug
        END,
        source_url = COALESCE(source_url, lot_url),
        detail_level = COALESCE(detail_level, 'detail')
        """
    )

    op.create_table(
        "region_sync_states",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("city_slug", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("requested_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("lots_discovered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ready_lots", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_region_sync_states_city_slug"), "region_sync_states", ["city_slug"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_region_sync_states_city_slug"), table_name="region_sync_states")
    op.drop_table("region_sync_states")

    with op.batch_alter_table("processed_lots", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_processed_lots_region_slug"))
        batch_op.drop_index(batch_op.f("ix_processed_lots_source_system"))
        batch_op.drop_column("detail_level")
        batch_op.drop_column("source_url")
        batch_op.drop_column("region_name")
        batch_op.drop_column("region_slug")
        batch_op.drop_column("source_system")
