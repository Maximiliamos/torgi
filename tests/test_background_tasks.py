from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from celery.exceptions import Retry
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from bankrotai import api, tasks
from bankrotai.auth import AuthenticatedUser
from bankrotai.db import Base, BackgroundTaskState
from bankrotai.services.ingestion import SyncAlreadyRunningError


client = TestClient(api.app)


@pytest.fixture(autouse=True)
def _operator_auth():
    api.app.dependency_overrides[api.require_admin] = lambda: AuthenticatedUser(
        id=1,
        username="operator",
        role="admin",
    )
    yield
    api.app.dependency_overrides.pop(api.require_admin, None)


def test_excessive_synchronous_get_is_rejected() -> None:
    response = client.get("/api/online/torgi-gov/lots", params={"all_pages": "true"})
    assert response.status_code == 422
    assert "POST /api/online/torgi-gov/sync" in response.json()["detail"]


def test_excessive_synchronous_page_is_rejected() -> None:
    assert client.get("/api/online/torgi-gov/lots", params={"page": 101}).status_code == 422


def test_bulk_start_returns_task_id(monkeypatch) -> None:
    monkeypatch.setattr(api, "schedule_bulk_torgi_sync", lambda filters, max_items: "task-123")
    response = client.post("/api/online/torgi-gov/sync", json={"search": "земля", "max_items": 500})
    assert response.status_code == 202
    assert response.json() == {"task_id": "task-123", "status": "queued"}


def test_unavailable_queue_returns_503(monkeypatch) -> None:
    def unavailable(*_args, **_kwargs):
        raise tasks.QueueUnavailableError("queue unavailable")

    monkeypatch.setattr(api, "schedule_bulk_torgi_sync", unavailable)
    assert client.post("/api/online/torgi-gov/sync", json={}).status_code == 503


def test_bulk_schedule_persists_queued_state_before_publish(monkeypatch) -> None:
    events = []
    monkeypatch.setattr(tasks, "broker_is_available", lambda: True)
    monkeypatch.setattr(tasks, "uuid", lambda: "task-before-publish")
    monkeypatch.setattr(tasks, "_set_task_state", lambda task_id, **values: events.append(("state", task_id, values)))
    monkeypatch.setattr(
        tasks.bulk_torgi_gov_sync_task,
        "apply_async",
        lambda **values: events.append(("publish", values)),
    )

    assert tasks.schedule_bulk_torgi_sync({"search_text": "smoke"}, 1) == "task-before-publish"
    assert events[0][0:2] == ("state", "task-before-publish")
    assert events[0][2]["status"] == "queued"
    assert events[1] == (
        "publish",
        {"args": [{"search_text": "smoke"}, 1], "task_id": "task-before-publish"},
    )


def test_bulk_schedule_records_publish_failure(monkeypatch) -> None:
    states = []
    monkeypatch.setattr(tasks, "broker_is_available", lambda: True)
    monkeypatch.setattr(tasks, "uuid", lambda: "task-publish-failed")
    monkeypatch.setattr(tasks, "_set_task_state", lambda task_id, **values: states.append((task_id, values)))
    monkeypatch.setattr(
        tasks.bulk_torgi_gov_sync_task,
        "apply_async",
        lambda **_values: (_ for _ in ()).throw(ConnectionError("redis unavailable")),
    )

    with pytest.raises(tasks.QueueUnavailableError):
        tasks.schedule_bulk_torgi_sync({}, 1)

    assert [values["status"] for _, values in states] == ["queued", "failed"]
    assert states[-1][1]["error"] == "redis unavailable"


def test_nationwide_sync_start_returns_queued_task(monkeypatch) -> None:
    monkeypatch.setattr(api, "schedule_nationwide_lot_sync", lambda **_kwargs: "sync-123")
    response = client.post("/api/sync/lots")
    assert response.status_code == 202
    assert response.json() == {"task_id": "sync-123", "status": "queued"}


def test_source_only_sync_mode_uses_a_single_source_spec(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(tasks, "run_nationwide_sync", lambda _sessions, _run_id, specs: captured.update(specs=specs) or {})
    tasks.nationwide_lot_sync_task.run("run-123", "source:bidexpert.ru")
    assert len(captured["specs"]) == 1
    assert captured["specs"][0].source_id == "bidexpert.ru"
    assert captured["specs"][0].reconcile_missing is True


def test_completed_ingestion_survives_map_dirty_mark_failure(monkeypatch) -> None:
    class FakeRedis:
        def close(self):
            pass

    monkeypatch.setattr(
        tasks,
        "run_nationwide_sync",
        lambda *_args: {"status": "success", "sources": [{"items_inserted": 1}]},
    )
    monkeypatch.setattr("redis.Redis.from_url", lambda *_args, **_kwargs: FakeRedis())
    monkeypatch.setattr(
        tasks,
        "_mark_map_dirty",
        lambda _client: (_ for _ in ()).throw(ConnectionError("redis unavailable")),
    )

    result = tasks.nationwide_lot_sync_task.run("run-without-database-row", "source:bidexpert.ru")

    assert result["status"] == "success"
    assert result["map_dataset_build"]["status"] == "dirty_mark_failed"
    assert "redis unavailable" in result["map_dataset_build"]["error"]


def test_source_only_schedule_uses_schema_safe_trigger_type(monkeypatch) -> None:
    class FakeService:
        def __init__(self, _session_factory):
            pass

        def create_run(self, **kwargs):
            assert kwargs["trigger_type"] == "manual_source_full"
            assert len(kwargs["trigger_type"]) <= 20
            assert kwargs["total_sources"] == 1
            return "source-run"

    monkeypatch.setattr(tasks, "broker_is_available", lambda: True)
    monkeypatch.setattr(tasks, "NationwideIngestionService", FakeService)
    captured_dispatch = {}
    monkeypatch.setattr(
        tasks.nationwide_lot_sync_task,
        "apply_async",
        lambda **kwargs: captured_dispatch.update(kwargs),
    )
    assert tasks.schedule_nationwide_lot_sync(triggered_by="test", mode="source:bidexpert.ru") == "source-run"
    assert captured_dispatch["soft_time_limit"] == tasks._DURABLE_NATIONWIDE_SOFT_TIME_LIMIT_SECONDS
    assert captured_dispatch["time_limit"] == tasks._DURABLE_NATIONWIDE_HARD_TIME_LIMIT_SECONDS


def test_duplicate_nationwide_sync_returns_existing_task(monkeypatch) -> None:
    def duplicate(**_kwargs):
        raise SyncAlreadyRunningError("sync-running")

    monkeypatch.setattr(api, "schedule_nationwide_lot_sync", duplicate)
    response = client.post("/api/sync/lots")
    assert response.status_code == 409
    assert response.json() == {"task_id": "sync-running", "status": "already_running"}


def test_transient_errors_are_classified_for_retry() -> None:
    assert tasks._is_transient_sync_error(RuntimeError("HTTP 503 upstream unavailable"))
    assert tasks._is_transient_sync_error(TimeoutError("read timeout"))
    assert not tasks._is_transient_sync_error(RuntimeError("HTTP 400 invalid filter"))
    assert not tasks._is_transient_sync_error(RuntimeError("HTTP 401"))


def test_p7_running_campaign_becomes_stale_after_acceptance_monitor_plus_grace() -> None:
    now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    stale = SimpleNamespace(
        status="running",
        started_at=(now - tasks._P7_RUNNING_STALE_AFTER - timedelta(seconds=1)).replace(tzinfo=None),
        created_at=(now - timedelta(days=1)).replace(tzinfo=None),
    )
    fresh = SimpleNamespace(
        status="running",
        started_at=(now - tasks._P7_RUNNING_STALE_AFTER + timedelta(seconds=1)).replace(tzinfo=None),
        created_at=(now - timedelta(days=1)).replace(tzinfo=None),
    )
    queued = SimpleNamespace(
        status="queued",
        started_at=None,
        created_at=(now - timedelta(days=1)).replace(tzinfo=None),
    )

    assert tasks._p7_campaign_is_stale(stale, now=now)
    assert not tasks._p7_campaign_is_stale(fresh, now=now)
    assert not tasks._p7_campaign_is_stale(queued, now=now)

    assert tasks._P7_RUNNING_STALE_AFTER < timedelta(seconds=tasks._P7_TASK_SOFT_TIME_LIMIT_SECONDS)
    assert tasks._P7_RUNNING_STALE_AFTER == timedelta(
        seconds=tasks._P7_WORKFLOW_MONITOR_SECONDS + tasks._P7_STALE_GRACE_SECONDS
    )



def test_schedule_p7_completes_existing_running_campaign_when_plan_is_empty(monkeypatch) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as session:
        session.add(
            BackgroundTaskState(
                task_id="p7-existing-empty",
                task_type="geocoding_fast_drain",
                status="running",
                started_at=now,
                progress_json={"phase": "draining", "processed": 0, "geocoded": 0},
            )
        )
        session.commit()

    @contextmanager
    def scope():
        with SessionLocal() as session:
            yield session
            session.commit()

    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "broker_is_available", lambda: True)
    monkeypatch.setattr(
        "bankrotai.services.geo_fast_drain.geo_fast_drain_plan",
        lambda _session: {
            "legacy_total": 0,
            "legacy_by_classification": {},
            "legacy_cfo": 0,
            "p7_held": 0,
        },
    )
    monkeypatch.setattr(
        tasks.geo_fast_drain_task,
        "apply_async",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("no task should be queued")),
    )

    task_id = tasks.schedule_geo_fast_drain()

    with SessionLocal() as session:
        state = session.query(BackgroundTaskState).filter_by(task_id=task_id).one()

    assert task_id == "p7-existing-empty"
    assert state.status == "completed"
    assert state.finished_at is not None
    assert state.error_message is None
    assert state.progress_json["phase"] == "completed"
    assert state.progress_json["stop_reason"] == "nothing_to_drain"
    assert state.progress_json["p7_total"] == 0
    assert state.result_json["p7_total"] == 0


def test_schedule_p7_keeps_fresh_running_campaign_when_plan_has_work(monkeypatch) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as session:
        session.add(
            BackgroundTaskState(
                task_id="p7-existing-work",
                task_type="geocoding_fast_drain",
                status="running",
                started_at=now,
                progress_json={"phase": "draining"},
            )
        )
        session.commit()

    @contextmanager
    def scope():
        with SessionLocal() as session:
            yield session
            session.commit()

    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "broker_is_available", lambda: True)
    monkeypatch.setattr(
        "bankrotai.services.geo_fast_drain.geo_fast_drain_plan",
        lambda _session: {
            "legacy_total": 1,
            "legacy_by_classification": {"network": 1},
            "legacy_cfo": 0,
            "p7_held": 0,
        },
    )

    task_id = tasks.schedule_geo_fast_drain()

    with SessionLocal() as session:
        state = session.query(BackgroundTaskState).filter_by(task_id=task_id).one()

    assert task_id == "p7-existing-work"
    assert state.status == "running"


def test_automatic_nationwide_refresh_uses_existing_run_lease(monkeypatch) -> None:
    created: dict = {}

    class FakeService:
        def __init__(self, _session_factory):
            pass

        def create_run(self, **kwargs):
            created.update(kwargs)
            return "scheduled-run"

    monkeypatch.setattr(tasks, "NationwideIngestionService", FakeService)
    monkeypatch.setattr(
        tasks.nationwide_lot_sync_task,
        "run",
        lambda run_id, mode: {"status": "partial", "mode": mode, "run_id": run_id},
    )

    result = tasks.automatic_nationwide_lot_refresh_task.run("fast")

    assert result == {"status": "partial", "mode": "fast", "run_id": "scheduled-run"}
    assert created == {
        "triggered_by": "celery-beat",
        "trigger_type": "scheduled_fast",
        "total_sources": 4,
    }


def test_automatic_nationwide_refresh_skips_existing_run(monkeypatch) -> None:
    class FakeService:
        def __init__(self, _session_factory):
            pass

        def create_run(self, **_kwargs):
            raise SyncAlreadyRunningError("active-run")

    monkeypatch.setattr(tasks, "NationwideIngestionService", FakeService)

    assert tasks.automatic_nationwide_lot_refresh_task.run("fast") == {
        "status": "skipped",
        "reason": "already_running",
        "run_id": "active-run",
    }


def test_automatic_full_refresh_retries_an_active_lease(monkeypatch) -> None:
    class FakeService:
        def __init__(self, _session_factory):
            pass

        def create_run(self, **_kwargs):
            raise SyncAlreadyRunningError("fast-run")

    captured: dict = {}

    def retry(**kwargs):
        captured.update(kwargs)
        raise Retry()

    monkeypatch.setattr(tasks, "NationwideIngestionService", FakeService)
    monkeypatch.setattr(tasks.automatic_nationwide_lot_refresh_task, "retry", retry)

    with pytest.raises(Retry):
        tasks.automatic_nationwide_lot_refresh_task.run("full")

    assert captured["countdown"] == tasks._NATIONWIDE_BUSY_RETRY_SECONDS
    assert captured["max_retries"] == tasks._NATIONWIDE_BUSY_RETRY_MAX_RETRIES


def test_partial_refresh_schedules_only_failed_source_retries(monkeypatch) -> None:
    class FakeService:
        def __init__(self, _session_factory):
            pass

        def create_run(self, **_kwargs):
            return "scheduled-run"

    queued: list[tuple[str, str, int]] = []

    class Queued:
        id = "source-retry-1"

    monkeypatch.setattr(tasks, "NationwideIngestionService", FakeService)
    monkeypatch.setattr(
        tasks.nationwide_lot_sync_task,
        "run",
        lambda _run_id, _mode: {
            "status": "partial",
            "sources": [
                {"source_system": "torgi.gov.ru", "status": "success"},
                {"source_system": "bidexpert.ru", "status": "failed", "error": "HTTP 503 connection timeout"},
                {"source_system": "bidexpert.ru", "status": "failed", "error": "HTTP 503 connection timeout"},
            ],
        },
    )
    monkeypatch.setattr(
        tasks.automatic_nationwide_source_retry_task,
        "apply_async",
        lambda *, args, countdown: queued.append((args[0], args[1], countdown)) or Queued(),
    )

    result = tasks.automatic_nationwide_lot_refresh_task.run("fast")

    assert len(queued) == 1
    assert queued[0][0:2] == ("bidexpert.ru", "fast")
    assert 60 <= queued[0][2] <= 66
    retry = result["targeted_source_retries"][0]
    assert retry["source_system"] == "bidexpert.ru"
    assert retry["status"] == "queued"
    assert retry["task_id"] == "source-retry-1"
    assert retry["countdown_seconds"] == queued[0][2]
    assert retry["error_category"] == "http_5xx"


def test_automatic_refresh_skips_while_global_source_network_circuit_is_open(monkeypatch) -> None:
    monkeypatch.setattr(tasks, "_global_source_network_blocked", lambda: True)

    result = tasks.automatic_nationwide_lot_refresh_task.run("fast")

    assert result == {
        "status": "skipped",
        "reason": "global_source_network_circuit",
        "mode": "fast",
    }


def test_targeted_source_retry_skips_while_circuit_is_open(monkeypatch) -> None:
    monkeypatch.setattr(tasks, "_source_is_circuit_blocked", lambda source: source == "bidexpert.ru")

    result = tasks.automatic_nationwide_source_retry_task.run("bidexpert.ru", "fast")

    assert result == {
        "status": "skipped",
        "reason": "source_circuit_open",
        "source_system": "bidexpert.ru",
    }


def test_targeted_source_retry_uses_one_source_and_category_aware_requeue(monkeypatch) -> None:
    created: dict = {}
    queued: list[tuple[str, str, int]] = []

    class FakeService:
        def __init__(self, _session_factory):
            pass

        def create_run(self, **kwargs):
            created.update(kwargs)
            return "retry-run"

    class Queued:
        id = "retry-again"

    monkeypatch.setattr(tasks, "NationwideIngestionService", FakeService)
    monkeypatch.setattr(
        tasks.nationwide_lot_sync_task,
        "run",
        lambda _run_id, _mode: {
            "status": "failed",
            "sources": [
                {
                    "source_system": "bidexpert.ru",
                    "status": "failed",
                    "error": "HTTP 503 upstream unavailable",
                }
            ],
        },
    )
    monkeypatch.setattr(
        tasks.automatic_nationwide_source_retry_task,
        "apply_async",
        lambda *, args, countdown: queued.append((args[0], args[1], countdown)) or Queued(),
    )
    monkeypatch.setattr(
        tasks.automatic_nationwide_source_retry_task,
        "retry",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("generic retry must not run")),
    )

    result = tasks.automatic_nationwide_source_retry_task.run("bidexpert.ru", "full")

    assert created == {
        "triggered_by": "celery-beat",
        "trigger_type": "scheduled_retry",
        "total_sources": 1,
    }
    assert result["status"] == "failed"
    assert len(queued) == 1
    assert queued[0][0:2] == ("bidexpert.ru", "full")
    assert 60 <= queued[0][2] <= 66
    assert result["targeted_source_retries"][0]["source_system"] == "bidexpert.ru"
    assert result["targeted_source_retries"][0]["error_category"] == "http_5xx"


def test_recovery_probe_queues_safe_fast_retry(monkeypatch) -> None:
    from bankrotai.services import source_resilience

    @contextmanager
    def fake_scope():
        yield object()

    class Queued:
        id = "recovered-fast-retry"

    queued: list[tuple[list[str], int]] = []
    monkeypatch.setattr(tasks, "session_scope", fake_scope)
    monkeypatch.setattr(tasks, "_source_is_paused", lambda _source: False)
    monkeypatch.setattr(source_resilience, "sources_due_for_probe", lambda _session: ["bidexpert.ru"])
    monkeypatch.setattr(
        source_resilience,
        "probe_source_endpoint",
        lambda source: {"success": True, "source_system": source, "http_status": 200},
    )
    monkeypatch.setattr(
        source_resilience,
        "record_source_probe",
        lambda _session, source, **_kwargs: {
            "source_system": source,
            "circuit_state": "closed",
            "next_retry_at": None,
        },
    )
    monkeypatch.setattr(
        tasks.automatic_nationwide_source_retry_task,
        "apply_async",
        lambda *, args, countdown: queued.append((args, countdown)) or Queued(),
    )

    result = tasks.probe_source_network_health_task.run()

    assert queued == [(["bidexpert.ru", "fast"], 5)]
    assert result["checked"] == 1
    assert result["results"][0]["status"] == "recovered"
    assert result["results"][0]["recovery_retry"] == {
        "status": "queued",
        "task_id": "recovered-fast-retry",
        "countdown_seconds": 5,
        "mode": "fast",
    }


def test_targeted_fast_source_retry_cannot_reconcile_or_archive(monkeypatch) -> None:
    captured: dict = {}
    bounded_spec = next(spec for spec in tasks.fast_source_specs(gis_publish_date_from="2026-09-25") if spec.source_id == "bidexpert.ru")

    class FakeService:
        def __init__(self, _session_factory):
            pass

        def create_run(self, **_kwargs):
            return "fast-retry-run"

    monkeypatch.setattr(tasks, "NationwideIngestionService", FakeService)
    monkeypatch.setattr(tasks, "_fast_nationwide_source_specs", lambda: (bounded_spec,))
    monkeypatch.setattr(
        tasks,
        "run_nationwide_sync",
        lambda _sessions, _run_id, specs: captured.update(specs=specs) or {"status": "success", "sources": []},
    )

    result = tasks.automatic_nationwide_source_retry_task.run("bidexpert.ru", "fast")

    assert result["status"] == "success"
    assert len(captured["specs"]) == 1
    assert captured["specs"][0].source_id == "bidexpert.ru"
    assert captured["specs"][0].reconcile_missing is False
    assert captured["specs"][0].max_batches == 1


def test_noop_ingestion_skips_map_build(monkeypatch) -> None:
    monkeypatch.setattr(
        tasks,
        "run_nationwide_sync",
        lambda *_args: {
            "status": "success",
            "sources": [
                {
                    "items_inserted": 0,
                    "items_updated": 0,
                    "items_archived": 0,
                    "duplicates_merged": 0,
                }
            ],
        },
    )
    monkeypatch.setattr(
        tasks,
        "_schedule_map_dataset_build",
        lambda: (_ for _ in ()).throw(AssertionError("no-op sync must not rebuild the map")),
    )

    result = tasks.nationwide_lot_sync_task.run("run-without-database-row", "source:bidexpert.ru")

    assert result["map_dataset_build"] == {"status": "skipped", "reason": "no-map-affecting-source-changes"}


class _Session:
    pass


@contextmanager
def _session_scope():
    yield _Session()


def test_production_does_not_use_thread_fallback(monkeypatch) -> None:
    monkeypatch.setattr(tasks, "init_db", lambda: None)
    monkeypatch.setattr(tasks, "session_scope", _session_scope)
    monkeypatch.setattr(tasks, "get_region_sync_state", lambda session, slug: None)
    monkeypatch.setattr(tasks, "broker_is_available", lambda: False)
    monkeypatch.setattr(tasks.settings, "allow_local_task_fallback", False)
    with pytest.raises(tasks.QueueUnavailableError):
        tasks.schedule_region_sync("yaroslavl")


def test_desktop_can_explicitly_enable_local_fallback(monkeypatch) -> None:
    started = []

    class FakeThread:
        def __init__(self, **kwargs):
            self.daemon = kwargs["daemon"]

        def start(self):
            started.append(True)

    monkeypatch.setattr(tasks, "init_db", lambda: None)
    monkeypatch.setattr(tasks, "session_scope", _session_scope)
    monkeypatch.setattr(tasks, "get_region_sync_state", lambda session, slug: None)
    monkeypatch.setattr(tasks, "upsert_region_sync_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks, "broker_is_available", lambda: False)
    monkeypatch.setattr(tasks.settings, "allow_local_task_fallback", True)
    monkeypatch.setattr(tasks.threading, "Thread", FakeThread)
    assert tasks.schedule_region_sync("yaroslavl") == "started-in-thread"
    assert started == [True]


def test_nationwide_tasks_use_progress_watchdog_with_emergency_ceiling() -> None:
    for task in (
        tasks.nationwide_lot_sync_task,
        tasks.automatic_nationwide_lot_refresh_task,
        tasks.automatic_nationwide_source_retry_task,
    ):
        assert task.soft_time_limit == tasks._DURABLE_NATIONWIDE_SOFT_TIME_LIMIT_SECONDS
        assert task.time_limit == tasks._DURABLE_NATIONWIDE_HARD_TIME_LIMIT_SECONDS


def test_paused_source_is_not_retried(monkeypatch) -> None:
    monkeypatch.setattr(tasks, "_source_is_paused", lambda source: source == "tbankrot.ru")
    queued = []
    monkeypatch.setattr(
        tasks.automatic_nationwide_source_retry_task,
        "apply_async",
        lambda **kwargs: queued.append(kwargs),
    )
    result = tasks._schedule_partial_source_retries(
        {"sources": [{"source_system": "tbankrot.ru", "status": "failed"}]},
        source_mode="fast",
    )
    assert queued == []
    assert result == [{"source_system": "tbankrot.ru", "status": "skipped", "reason": "source_paused"}]


def test_targeted_retry_skips_paused_source(monkeypatch) -> None:
    monkeypatch.setattr(tasks, "_source_is_paused", lambda source: source == "tbankrot.ru")
    assert tasks.automatic_nationwide_source_retry_task.run("tbankrot.ru", "fast") == {
        "status": "skipped",
        "reason": "source_paused",
        "source_system": "tbankrot.ru",
    }


def test_tbankrot_is_paused_by_default_even_without_database_setting(monkeypatch) -> None:
    captured = {}

    def fake_get(key, default):
        captured["key"] = key
        captured["default"] = default
        return default

    monkeypatch.setattr(tasks, "get_app_setting", fake_get)

    assert tasks._source_is_paused("tbankrot.ru") is True
    assert captured == {"key": "source_paused:tbankrot.ru", "default": "true"}
    assert tasks._source_is_paused("bidexpert.ru") is False
