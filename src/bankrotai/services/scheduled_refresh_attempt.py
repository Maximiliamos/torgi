from __future__ import annotations

from typing import Any

from sqlalchemy.orm import sessionmaker

from bankrotai.core import utc_now
from bankrotai.db import LotSyncRun
from bankrotai.services.ingestion import NationwideIngestionService


def scheduled_trigger_type(mode: str) -> str:
    return "scheduled_incr" if mode == "incremental" else f"scheduled_{mode}"


class ScheduledRefreshAttempt:
    """Durable wrapper around a 09:00/21:00 scheduled source attempt."""

    def __init__(
        self,
        *,
        run_id: str,
        mode: str,
        session_factory: sessionmaker,
    ) -> None:
        self.run_id = run_id
        self.mode = mode
        self.session_factory = session_factory
        self.trigger_type = scheduled_trigger_type(mode)

    @classmethod
    def create(
        cls,
        *,
        mode: str,
        total_sources: int,
        session_factory: sessionmaker,
    ) -> "ScheduledRefreshAttempt":
        service = NationwideIngestionService(session_factory)
        run_id = service.create_run(
            triggered_by="celery-beat",
            trigger_type=scheduled_trigger_type(mode),
            total_sources=total_sources,
        )
        return cls(run_id=run_id, mode=mode, session_factory=session_factory)

    def persist_preflight(self, preflight: dict[str, Any]) -> None:
        now = utc_now()
        with self.session_factory() as session:
            run = session.get(LotSyncRun, self.run_id)
            if run is None:
                return
            checkpoint = dict(run.checkpoint_json or {})
            checkpoint.update(
                {
                    "phase": "preflight_complete",
                    "preflight_completed_at": now.isoformat(),
                    "preflight": preflight,
                }
            )
            run.checkpoint_json = checkpoint
            run.heartbeat_at = now
            session.commit()

    def finalize_skip(
        self,
        *,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        payload: dict[str, Any] = {
            "status": "skipped",
            "reason": reason,
            "mode": self.mode,
        }
        if details:
            payload.update(details)
        with self.session_factory() as session:
            run = session.get(LotSyncRun, self.run_id)
            if run is not None:
                run.status = "skipped"
                run.finished_at = now
                run.heartbeat_at = now
                run.lease_expires_at = None
                run.result_json = payload
                run.error_message = f"Scheduled run skipped: {reason}"[:2000]
                checkpoint = dict(run.checkpoint_json or {})
                checkpoint.update(
                    {
                        "phase": "skipped_before_ingestion",
                        "skip_reason": reason,
                        "finished_at": now.isoformat(),
                    }
                )
                run.checkpoint_json = checkpoint
                session.commit()
        return {"run_id": self.run_id, **payload}

    def attach_preflight(
        self,
        preflight: dict[str, Any],
        *,
        warning: bool,
    ) -> None:
        """Restore preflight evidence after ingestion finalizes result_json."""
        with self.session_factory() as session:
            run = session.get(LotSyncRun, self.run_id)
            if run is None:
                return
            result = dict(run.result_json or {})
            result["preflight"] = preflight
            if warning:
                result["preflight_warning"] = "source_preflight_failed"
            run.result_json = result
            checkpoint = dict(run.checkpoint_json or {})
            checkpoint["preflight"] = preflight
            checkpoint["preflight_warning"] = bool(warning)
            run.checkpoint_json = checkpoint
            session.commit()
