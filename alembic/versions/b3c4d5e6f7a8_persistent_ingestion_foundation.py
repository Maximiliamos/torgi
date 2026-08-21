"""persistent ingestion foundation

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from bankrotai.regions import REGION_DIRECTORY


revision: str = "b3c4d5e6f7a8"
down_revision: str | None = "a2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lot_sync_runs",
        sa.Column("id", sa.String(length=100), nullable=False),
        sa.Column("triggered_by", sa.String(length=100)),
        sa.Column("trigger_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("total_sources", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("finished_at", sa.DateTime()),
        sa.Column("heartbeat_at", sa.DateTime()),
        sa.Column("lease_owner", sa.String(length=200)),
        sa.Column("lease_expires_at", sa.DateTime()),
        sa.Column("checkpoint_json", sa.JSON()),
        sa.Column("result_json", sa.JSON()),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("triggered_by", "trigger_type", "status", "heartbeat_at", "lease_owner", "lease_expires_at"):
        op.create_index(f"ix_lot_sync_runs_{column}", "lot_sync_runs", [column])

    op.create_table(
        "lot_sync_source_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("sync_run_id", sa.String(length=100), nullable=False),
        sa.Column("source_system", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("complete_source_run", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pages_scanned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_inserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_unchanged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_archived", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("geocoded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicates_merged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("error_message", sa.Text()),
        sa.Column("checkpoint_json", sa.JSON()),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("finished_at", sa.DateTime()),
        sa.ForeignKeyConstraint(["sync_run_id"], ["lot_sync_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sync_run_id", "source_system", name="uq_lot_sync_source_run"),
    )
    for column in ("sync_run_id", "source_system", "status"):
        op.create_index(f"ix_lot_sync_source_runs_{column}", "lot_sync_source_runs", [column])

    region_table = op.create_table(
        "region_directory",
        sa.Column("code", sa.String(length=3), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("gis_torgi_code", sa.String(length=100)),
        sa.Column("tbankrot_code", sa.String(length=100)),
        sa.Column("torgi_russia_code", sa.String(length=100)),
        sa.Column("lot_online_code", sa.String(length=100)),
        sa.PrimaryKeyConstraint("code"),
        sa.UniqueConstraint("name", name="uq_region_directory_name"),
    )
    op.bulk_insert(
        region_table,
        [
            {
                "code": region.code,
                "name": region.name,
                "aliases": list(region.aliases),
                "gis_torgi_code": region.gis_torgi_code,
                "tbankrot_code": region.tbankrot_code,
                "torgi_russia_code": region.torgi_russia_code,
                "lot_online_code": region.lot_online_code,
            }
            for region in REGION_DIRECTORY
        ],
    )

    with op.batch_alter_table("processed_lots") as batch:
        batch.add_column(sa.Column("region_code", sa.String(length=3)))
        batch.create_index("ix_processed_lots_region_code", ["region_code"])
    with op.batch_alter_table("canonical_lots") as batch:
        batch.add_column(sa.Column("region_code", sa.String(length=3)))
        batch.create_index("ix_canonical_lots_region_code", ["region_code"])
    with op.batch_alter_table("source_lots") as batch:
        batch.add_column(sa.Column("lot_url", sa.Text()))
        batch.add_column(sa.Column("etp_url", sa.Text()))
        batch.add_column(sa.Column("title", sa.Text()))
        batch.add_column(sa.Column("description", sa.Text()))
        batch.add_column(sa.Column("category", sa.String(length=50)))
        batch.add_column(sa.Column("region_code", sa.String(length=3)))
        batch.add_column(sa.Column("region_name", sa.String(length=200)))
        batch.add_column(sa.Column("address", sa.Text()))
        batch.add_column(sa.Column("cadastral_number", sa.String(length=50)))
        batch.add_column(sa.Column("start_price", sa.Numeric(15, 2)))
        batch.add_column(sa.Column("current_price", sa.Numeric(15, 2)))
        batch.add_column(sa.Column("source_status", sa.String(length=100)))
        batch.add_column(sa.Column("published_at", sa.DateTime()))
        batch.add_column(sa.Column("source_updated_at", sa.DateTime()))
        batch.add_column(sa.Column("first_seen_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
        batch.add_column(sa.Column("last_sync_run_id", sa.String(length=100)))
        batch.add_column(sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("archived_at", sa.DateTime()))
        batch.add_column(sa.Column("archive_reason", sa.String(length=200)))
        batch.add_column(sa.Column("missing_successful_runs", sa.Integer(), nullable=False, server_default="0"))
        batch.create_foreign_key(
            "fk_source_lots_last_sync_run_id_lot_sync_runs",
            "lot_sync_runs",
            ["last_sync_run_id"],
            ["id"],
            ondelete="SET NULL",
        )
        for column in (
            "category",
            "region_code",
            "cadastral_number",
            "start_price",
            "source_status",
            "published_at",
            "first_seen_at",
            "last_sync_run_id",
            "is_active",
            "is_archived",
        ):
            batch.create_index(f"ix_source_lots_{column}", [column])
        batch.create_index(
            "ix_source_lots_active_region_price",
            ["is_active", "is_archived", "region_code", "start_price"],
        )


def downgrade() -> None:
    with op.batch_alter_table("source_lots") as batch:
        batch.drop_index("ix_source_lots_active_region_price")
        for column in reversed((
            "category", "region_code", "cadastral_number", "start_price", "source_status",
            "published_at", "first_seen_at", "last_sync_run_id", "is_active", "is_archived",
        )):
            batch.drop_index(f"ix_source_lots_{column}")
        batch.drop_constraint("fk_source_lots_last_sync_run_id_lot_sync_runs", type_="foreignkey")
        for column in reversed((
            "lot_url", "etp_url", "title", "description", "category", "region_code", "region_name",
            "address", "cadastral_number", "start_price", "current_price", "source_status", "published_at",
            "source_updated_at", "first_seen_at", "last_sync_run_id", "is_active", "is_archived",
            "archived_at", "archive_reason", "missing_successful_runs",
        )):
            batch.drop_column(column)
    with op.batch_alter_table("canonical_lots") as batch:
        batch.drop_index("ix_canonical_lots_region_code")
        batch.drop_column("region_code")
    with op.batch_alter_table("processed_lots") as batch:
        batch.drop_index("ix_processed_lots_region_code")
        batch.drop_column("region_code")
    op.drop_table("region_directory")
    op.drop_table("lot_sync_source_runs")
    op.drop_table("lot_sync_runs")
