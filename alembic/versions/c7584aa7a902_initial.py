"""initial

Revision ID: c7584aa7a902
Revises:
Create Date: 2026-04-16 19:34:42.316249

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c7584aa7a902"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "raw_lots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("external_id", sa.String(length=100), nullable=False),
        sa.Column("raw_data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_raw_lots_external_id"), "raw_lots", ["external_id"], unique=True)

    op.create_table(
        "processed_lots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("external_id", sa.String(length=100), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("cadastral_number", sa.String(length=50), nullable=True),
        sa.Column("vin", sa.String(length=20), nullable=True),
        sa.Column("start_price", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("current_price", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("auction_status", sa.String(length=20), nullable=False),
        sa.Column("market_price", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("market_price_min", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("market_price_max", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("discount_percent", sa.Float(), nullable=True),
        sa.Column("risk_score", sa.Integer(), nullable=True),
        sa.Column("ai_recommendation", sa.Text(), nullable=True),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("links_to_analogs", sa.JSON(), nullable=True),
        sa.Column("lot_url", sa.Text(), nullable=True),
        sa.Column("area", sa.Float(), nullable=True),
        sa.Column("needs_human_review", sa.Boolean(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("last_update", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_processed_lots_cadastral_number"), "processed_lots", ["cadastral_number"], unique=False)
    op.create_index(op.f("ix_processed_lots_external_id"), "processed_lots", ["external_id"], unique=True)
    op.create_index(op.f("ix_processed_lots_rating"), "processed_lots", ["rating"], unique=False)
    op.create_index(op.f("ix_processed_lots_vin"), "processed_lots", ["vin"], unique=False)

    op.create_table(
        "lot_status_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("lot_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("source_status", sa.String(length=100), nullable=False),
        sa.Column("normalized_status", sa.String(length=100), nullable=False),
        sa.Column("status_confidence", sa.String(length=20), nullable=False),
        sa.Column("trace_reason", sa.Text(), nullable=True),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("source_checked_at", sa.DateTime(), nullable=True),
        sa.Column("snapshot_ref", sa.String(length=100), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["lot_id"], ["processed_lots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_lot_status_events_lot_id"), "lot_status_events", ["lot_id"], unique=False)
    op.create_index(op.f("ix_lot_status_events_observed_at"), "lot_status_events", ["observed_at"], unique=False)

    op.create_table(
        "lot_status_observations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("lot_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("raw_status", sa.String(length=100), nullable=False),
        sa.Column("normalized_status", sa.String(length=100), nullable=False),
        sa.Column("is_winner", sa.Boolean(), nullable=False),
        sa.Column("observation_confidence", sa.String(length=20), nullable=False),
        sa.Column("checked_at", sa.DateTime(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["lot_id"], ["processed_lots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_lot_status_observations_lot_id"), "lot_status_observations", ["lot_id"], unique=False)
    op.create_index(op.f("ix_lot_status_observations_observed_at"), "lot_status_observations", ["observed_at"], unique=False)
    op.create_index(op.f("ix_lot_status_observations_source"), "lot_status_observations", ["source"], unique=False)

    op.create_table(
        "lot_price_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("lot_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("price_kind", sa.String(length=30), nullable=False),
        sa.Column("amount", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("source_checked_at", sa.DateTime(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["lot_id"], ["processed_lots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_lot_price_events_lot_id"), "lot_price_events", ["lot_id"], unique=False)
    op.create_index(op.f("ix_lot_price_events_observed_at"), "lot_price_events", ["observed_at"], unique=False)

    op.create_table(
        "source_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("lot_id", sa.Integer(), nullable=True),
        sa.Column("raw_lot_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("snapshot_kind", sa.String(length=50), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("checksum", sa.String(length=128), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["lot_id"], ["processed_lots.id"]),
        sa.ForeignKeyConstraint(["raw_lot_id"], ["raw_lots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_source_snapshots_lot_id"), "source_snapshots", ["lot_id"], unique=False)
    op.create_index(op.f("ix_source_snapshots_observed_at"), "source_snapshots", ["observed_at"], unique=False)
    op.create_index(op.f("ix_source_snapshots_raw_lot_id"), "source_snapshots", ["raw_lot_id"], unique=False)

    op.create_table(
        "lot_geo_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("lot_id", sa.Integer(), nullable=False),
        sa.Column("geo_source", sa.String(length=50), nullable=False),
        sa.Column("geo_method", sa.String(length=50), nullable=False),
        sa.Column("geo_confidence", sa.String(length=20), nullable=False),
        sa.Column("centroid_lat", sa.Float(), nullable=False),
        sa.Column("centroid_lon", sa.Float(), nullable=False),
        sa.Column("geometry_json", sa.JSON(), nullable=True),
        sa.Column("trace_reason", sa.Text(), nullable=True),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("source_checked_at", sa.DateTime(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["lot_id"], ["processed_lots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_lot_geo_snapshots_lot_id"), "lot_geo_snapshots", ["lot_id"], unique=False)
    op.create_index(op.f("ix_lot_geo_snapshots_observed_at"), "lot_geo_snapshots", ["observed_at"], unique=False)

    op.create_table(
        "valuation_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("lot_id", sa.Integer(), nullable=False),
        sa.Column("run_kind", sa.String(length=50), nullable=False),
        sa.Column("valuation_method", sa.String(length=100), nullable=False),
        sa.Column("valuation_confidence", sa.String(length=20), nullable=False),
        sa.Column("valuation_version", sa.String(length=50), nullable=False),
        sa.Column("valuation_sources", sa.JSON(), nullable=True),
        sa.Column("valuation_snapshot", sa.JSON(), nullable=True),
        sa.Column("needs_human_review", sa.Boolean(), nullable=False),
        sa.Column("appraised_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["lot_id"], ["processed_lots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_valuation_runs_appraised_at"), "valuation_runs", ["appraised_at"], unique=False)
    op.create_index(op.f("ix_valuation_runs_lot_id"), "valuation_runs", ["lot_id"], unique=False)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.String(length=100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_logs_user_id"), "audit_logs", ["user_id"], unique=False)

    op.create_table(
        "saved_searches",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_saved_searches_user_id"), "saved_searches", ["user_id"], unique=False)

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("target_id", sa.String(length=100), nullable=False),
        sa.Column("saved_search_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_notified_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["saved_search_id"], ["saved_searches.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_subscriptions_user_id"), "subscriptions", ["user_id"], unique=False)

    op.create_table(
        "watchlists",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_watchlists_user_id"), "watchlists", ["user_id"], unique=False)

    op.create_table(
        "watchlist_lots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("watchlist_id", sa.Integer(), nullable=False),
        sa.Column("lot_id", sa.Integer(), nullable=False),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["lot_id"], ["processed_lots.id"]),
        sa.ForeignKeyConstraint(["watchlist_id"], ["watchlists.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_watchlist_lots_lot_id"), "watchlist_lots", ["lot_id"], unique=False)
    op.create_index(op.f("ix_watchlist_lots_watchlist_id"), "watchlist_lots", ["watchlist_id"], unique=False)

    op.create_table(
        "lot_notes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("lot_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["lot_id"], ["processed_lots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_lot_notes_lot_id"), "lot_notes", ["lot_id"], unique=False)
    op.create_index(op.f("ix_lot_notes_user_id"), "lot_notes", ["user_id"], unique=False)

    op.create_table(
        "lot_workflow_states",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("lot_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("pipeline_stage", sa.String(length=50), nullable=False),
        sa.Column("calendar_due_at", sa.DateTime(), nullable=True),
        sa.Column("stage_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["lot_id"], ["processed_lots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_lot_workflow_states_lot_id"), "lot_workflow_states", ["lot_id"], unique=False)
    op.create_index(op.f("ix_lot_workflow_states_user_id"), "lot_workflow_states", ["user_id"], unique=False)

    op.create_table(
        "workflow_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("lot_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("task_status", sa.String(length=30), nullable=False),
        sa.Column("priority", sa.String(length=20), nullable=False),
        sa.Column("due_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["lot_id"], ["processed_lots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_workflow_tasks_lot_id"), "workflow_tasks", ["lot_id"], unique=False)
    op.create_index(op.f("ix_workflow_tasks_user_id"), "workflow_tasks", ["user_id"], unique=False)

    op.create_table(
        "lot_event_subscriptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("lot_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("target_id", sa.String(length=100), nullable=False),
        sa.Column("event_types", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_notified_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["lot_id"], ["processed_lots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_lot_event_subscriptions_lot_id"), "lot_event_subscriptions", ["lot_id"], unique=False)
    op.create_index(op.f("ix_lot_event_subscriptions_user_id"), "lot_event_subscriptions", ["user_id"], unique=False)

    op.create_table(
        "task_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_name", sa.String(length=100), nullable=False),
        sa.Column("task_key", sa.String(length=200), nullable=False),
        sa.Column("task_status", sa.String(length=30), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("max_retries", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_task_runs_started_at"), "task_runs", ["started_at"], unique=False)
    op.create_index(op.f("ix_task_runs_task_key"), "task_runs", ["task_key"], unique=False)
    op.create_index(op.f("ix_task_runs_task_name"), "task_runs", ["task_name"], unique=False)

    op.create_table(
        "task_errors",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_run_id", sa.Integer(), nullable=True),
        sa.Column("task_name", sa.String(length=100), nullable=False),
        sa.Column("task_key", sa.String(length=200), nullable=False),
        sa.Column("error_type", sa.String(length=120), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["task_run_id"], ["task_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_task_errors_created_at"), "task_errors", ["created_at"], unique=False)
    op.create_index(op.f("ix_task_errors_task_key"), "task_errors", ["task_key"], unique=False)
    op.create_index(op.f("ix_task_errors_task_name"), "task_errors", ["task_name"], unique=False)
    op.create_index(op.f("ix_task_errors_task_run_id"), "task_errors", ["task_run_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("task_errors")
    op.drop_table("task_runs")
    op.drop_table("lot_event_subscriptions")
    op.drop_table("workflow_tasks")
    op.drop_table("lot_workflow_states")
    op.drop_table("lot_notes")
    op.drop_table("watchlist_lots")
    op.drop_table("watchlists")
    op.drop_table("subscriptions")
    op.drop_table("saved_searches")
    op.drop_table("audit_logs")
    op.drop_table("valuation_runs")
    op.drop_table("lot_geo_snapshots")
    op.drop_table("source_snapshots")
    op.drop_table("lot_price_events")
    op.drop_table("lot_status_observations")
    op.drop_table("lot_status_events")
    op.drop_table("processed_lots")
    op.drop_table("raw_lots")
