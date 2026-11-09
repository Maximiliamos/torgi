from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from bankrotai.db import BackgroundTaskState
from bankrotai.services.geo_deploy_recovery import mark_interrupted_geo_tasks


def test_home_deploy_marks_only_running_geo_progress_interrupted() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    BackgroundTaskState.__table__.create(engine)
    with Session(engine) as session:
        session.add_all([
            BackgroundTaskState(task_id="geo-running", task_type="geocoding", status="running"),
            BackgroundTaskState(task_id="geo-queued", task_type="geocoding", status="queued"),
            BackgroundTaskState(task_id="geo-done", task_type="geo_backfill", status="completed"),
            BackgroundTaskState(task_id="other-running", task_type="maintenance", status="running"),
        ])
        session.commit()

        changed = mark_interrupted_geo_tasks(
            session, reason="interrupted_by_home_deploy:test-sha"
        )
        session.commit()

        assert changed == 1
        running_geo = session.query(BackgroundTaskState).filter_by(task_id="geo-running").one()
        assert running_geo.status == "failed"
        assert running_geo.finished_at is not None
        assert running_geo.error_message == "interrupted_by_home_deploy:test-sha"
        assert session.query(BackgroundTaskState).filter_by(task_id="geo-queued").one().status == "queued"
        assert session.query(BackgroundTaskState).filter_by(task_id="geo-done").one().status == "completed"
        assert session.query(BackgroundTaskState).filter_by(task_id="other-running").one().status == "running"
    engine.dispose()
