"""desktop reliability foundation

Revision ID: d9e0f1a2b3c4
Revises: c1d2e3f4a5b6
"""

from alembic import op
import sqlalchemy as sa


revision = "d9e0f1a2b3c4"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_health_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_system", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="unknown"),
        sa.Column("last_started_at", sa.DateTime()),
        sa.Column("last_success_at", sa.DateTime()),
        sa.Column("last_failure_at", sa.DateTime()),
        sa.Column("last_error", sa.Text()),
        sa.Column("items_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata_json", sa.JSON()),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("source_system", name="uq_source_health_states_source_system"),
    )
    op.create_index("ix_source_health_states_source_system", "source_health_states", ["source_system"])
    op.create_index("ix_source_health_states_status", "source_health_states", ["status"])

    op.create_table(
        "geo_failures",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lot_id", sa.Integer(), sa.ForeignKey("processed_lots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="queued"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("last_failed_at", sa.DateTime(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime()),
        sa.Column("resolved_at", sa.DateTime()),
        sa.UniqueConstraint("lot_id", name="uq_geo_failures_lot_id"),
    )
    op.create_index("ix_geo_failures_lot_id", "geo_failures", ["lot_id"])
    op.create_index("ix_geo_failures_status", "geo_failures", ["status"])
    op.create_index("ix_geo_failures_next_retry_at", "geo_failures", ["next_retry_at"])

    op.create_table(
        "duplicate_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("primary_lot_id", sa.Integer(), sa.ForeignKey("processed_lots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("secondary_lot_id", sa.Integer(), sa.ForeignKey("processed_lots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("user_id", sa.String(length=100), nullable=False, server_default="desktop"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_duplicate_reviews_primary_lot_id", "duplicate_reviews", ["primary_lot_id"])
    op.create_index("ix_duplicate_reviews_secondary_lot_id", "duplicate_reviews", ["secondary_lot_id"])
    op.create_index("ix_duplicate_reviews_action", "duplicate_reviews", ["action"])

    op.create_table(
        "saved_max_bid_scenarios",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lot_id", sa.Integer(), sa.ForeignKey("processed_lots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False, server_default="desktop"),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("inputs_json", sa.JSON(), nullable=False),
        sa.Column("results_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_saved_max_bid_scenarios_lot_id", "saved_max_bid_scenarios", ["lot_id"])
    op.create_index("ix_saved_max_bid_scenarios_user_id", "saved_max_bid_scenarios", ["user_id"])
    op.create_index("ix_saved_max_bid_scenarios_created_at", "saved_max_bid_scenarios", ["created_at"])

    op.create_table(
        "lot_document_changes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("lot_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_version_id", sa.Integer(), sa.ForeignKey("lot_document_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_version_id", sa.Integer(), sa.ForeignKey("lot_document_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("from_version_id", "to_version_id", name="uq_document_change_versions"),
    )
    op.create_index("ix_lot_document_changes_document_id", "lot_document_changes", ["document_id"])
    op.create_index("ix_lot_document_changes_from_version_id", "lot_document_changes", ["from_version_id"])
    op.create_index("ix_lot_document_changes_to_version_id", "lot_document_changes", ["to_version_id"])

    op.create_table(
        "diagnostic_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("severity", sa.String(length=20), nullable=False, server_default="info"),
        sa.Column("component", sa.String(length=100), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("context_json", sa.JSON()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_diagnostic_events_severity", "diagnostic_events", ["severity"])
    op.create_index("ix_diagnostic_events_component", "diagnostic_events", ["component"])
    op.create_index("ix_diagnostic_events_created_at", "diagnostic_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("diagnostic_events")
    op.drop_table("lot_document_changes")
    op.drop_table("saved_max_bid_scenarios")
    op.drop_table("duplicate_reviews")
    op.drop_table("geo_failures")
    op.drop_table("source_health_states")
