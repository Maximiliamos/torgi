from __future__ import annotations

from contextlib import contextmanager

import pytest
from celery.exceptions import Retry
from fastapi.testclient import TestClient

from bankrotai import api, tasks
from bankrotai.auth import AuthenticatedUser
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


def test_completed_ingestion_survives_map_build_queue_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        tasks,
        "run_nationwide_sync",
        lambda *_args: {"status": "success", "sources": [{"items_inserted": 1}]},
    )
    monkeypatch.setattr(
        tasks.build_map_dataset_task,
        "delay",
        lambda: (_ for _ in ()).throw(ConnectionError("queue unavailable")),
    )

    result = tasks.nationwide_lot_sync_task.run("run-without-database-row", "source:bidexpert.ru")

    assert result["status"] == "success"
    assert result["map_dataset_build"]["status"] == "schedule_failed"
    assert "queue unavailable" in result["map_dataset_build"]["error"]


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
    monkeypatch.setattr(tasks.nationwide_lot_sync_task, "apply_async", lambda **_kwargs: None)
    assert tasks.schedule_nationwide_lot_sync(triggered_by="test", mode="source:bidexpert.ru") == "source-run"


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
        "total_sources": 5,
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
                {"source_system": "tbankrot.ru", "status": "failed"},
                {"source_system": "tbankrot.ru", "status": "failed"},
            ],
        },
    )
    monkeypatch.setattr(
        tasks.automatic_nationwide_source_retry_task,
        "apply_async",
        lambda *, args, countdown: queued.append((args[0], args[1], countdown)) or Queued(),
    )

    result = tasks.automatic_nationwide_lot_refresh_task.run("fast")

    assert queued == [("tbankrot.ru", "fast", tasks._PARTIAL_SOURCE_RETRY_DELAY_SECONDS)]
    assert result["targeted_source_retries"] == [
        {
            "source_system": "tbankrot.ru",
            "status": "queued",
            "task_id": "source-retry-1",
            "countdown_seconds": tasks._PARTIAL_SOURCE_RETRY_DELAY_SECONDS,
        }
    ]


def test_targeted_source_retry_uses_one_source_and_bounded_retries(monkeypatch) -> None:
    created: dict = {}

    class FakeService:
        def __init__(self, _session_factory):
            pass

        def create_run(self, **kwargs):
            created.update(kwargs)
            return "retry-run"

    captured: dict = {}

    def retry(**kwargs):
        captured.update(kwargs)
        raise Retry()

    monkeypatch.setattr(tasks, "NationwideIngestionService", FakeService)
    monkeypatch.setattr(
        tasks.nationwide_lot_sync_task,
        "run",
        lambda _run_id, _mode: {"status": "failed", "sources": [{"source_system": "tbankrot.ru", "status": "failed"}]},
    )
    monkeypatch.setattr(tasks.automatic_nationwide_source_retry_task, "retry", retry)

    with pytest.raises(Retry):
        tasks.automatic_nationwide_source_retry_task.run("tbankrot.ru", "full")

    assert created == {
        "triggered_by": "celery-beat",
        "trigger_type": "scheduled_retry",
        "total_sources": 1,
    }
    assert captured["max_retries"] == tasks._NATIONWIDE_REFRESH_MAX_RETRIES


def test_targeted_fast_source_retry_cannot_reconcile_or_archive(monkeypatch) -> None:
    captured: dict = {}
    bounded_spec = tasks.fast_source_specs(gis_publish_date_from="2026-09-25")[1]

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

    result = tasks.automatic_nationwide_source_retry_task.run("tbankrot.ru", "fast")

    assert result["status"] == "success"
    assert len(captured["specs"]) == 1
    assert captured["specs"][0].source_id == "tbankrot.ru"
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
