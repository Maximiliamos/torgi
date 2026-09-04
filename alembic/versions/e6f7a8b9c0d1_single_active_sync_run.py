"""Enforce a single active nationwide synchronization run."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "e6f7a8b9c0d1"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    active_rows = connection.execute(
        sa.text(
            "SELECT id FROM lot_sync_runs "
            "WHERE status IN ('queued', 'running') "
            "ORDER BY created_at DESC, id DESC"
        )
    ).scalars().all()
    if len(active_rows) > 1:
        connection.execute(
            sa.text(
                "UPDATE lot_sync_runs SET status = 'failed', finished_at = CURRENT_TIMESTAMP, "
                "error_message = 'Superseded while enforcing the single-active-run invariant' "
                "WHERE status IN ('queued', 'running') AND id <> :winner"
            ),
            {"winner": active_rows[0]},
        )

    op.create_index(
        "uq_lot_sync_runs_single_active",
        "lot_sync_runs",
        [sa.literal_column("(1)")],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
        sqlite_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_lot_sync_runs_single_active", table_name="lot_sync_runs")
