"""Deployment recovery for GEO progress rows whose worker was intentionally replaced."""
from __future__ import annotations

import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from bankrotai.core import utc_now
from bankrotai.db import BackgroundTaskState, SessionLocal

_GEO_TASK_TYPES = ("geocoding", "geo_backfill")


def mark_interrupted_geo_tasks(
    session: Session,
    *,
    reason: str,
) -> int:
    """Fail only running GEO progress rows after their sole worker was terminated."""
    message = str(reason or "").strip()
    if not message:
        raise ValueError("reason is required")
    rows = session.scalars(
        select(BackgroundTaskState).where(
            BackgroundTaskState.task_type.in_(_GEO_TASK_TYPES),
            BackgroundTaskState.status == "running",
        )
    ).all()
    finished_at = utc_now()
    for row in rows:
        row.status = "failed"
        row.error_message = message[:2000]
        row.finished_at = finished_at
    session.flush()
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    with SessionLocal() as session:
        count = mark_interrupted_geo_tasks(session, reason=args.reason)
        session.commit()
    print(count)


if __name__ == "__main__":
    main()
