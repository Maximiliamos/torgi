from __future__ import annotations

from bankrotai import tasks
from bankrotai.services import geo_backfill, map_builder


class FakeRedis:
    values: dict[str, str] = {}

    @classmethod
    def from_url(cls, *_args, **_kwargs):
        return cls()

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def getdel(self, key: str):
        return self.values.pop(key, None)

    def close(self) -> None:
        return None


def test_scheduled_geocoding_uses_visible_progress_and_defers_map_build(monkeypatch) -> None:
    import redis

    captured: dict = {}

    def geocode(_factory, **kwargs):
        captured.update(kwargs)
        return {
            "queued": tasks._GEO_BATCH_LIMIT,
            "processed": tasks._GEO_BATCH_LIMIT,
            "geocoded": 100,
        }

    FakeRedis.values = {}
    monkeypatch.setattr(geo_backfill, "geocode_pending_lots", geocode)
    monkeypatch.setattr(redis, "Redis", FakeRedis)
    monkeypatch.setattr(
        tasks,
        "_schedule_dirty_map_publication",
        lambda: {"status": "deferred", "maximum_delay_seconds": 60},
    )
    monkeypatch.setattr(
        tasks,
        "_schedule_geocode_continuation",
        lambda: {"status": "queued", "task_id": "geo-next", "countdown_seconds": 2},
    )

    result = tasks.geocode_pending_lots_task.run()

    assert captured["limit"] == tasks._GEO_BATCH_LIMIT == 500
    assert captured["progress_task_id"].startswith("celery-")
    assert result["map_dataset_build"]["status"] == "deferred"
    assert result["map_dataset_build"]["maximum_delay_seconds"] == 60
    assert result["continuation"]["status"] == "queued"
    assert FakeRedis.values[tasks._MAP_DIRTY_KEY] == "1"


def test_partial_geocoding_batch_does_not_schedule_continuation(monkeypatch) -> None:
    import redis

    monkeypatch.setattr(
        geo_backfill,
        "geocode_pending_lots",
        lambda *_args, **_kwargs: {"queued": 37, "processed": 37, "geocoded": 0},
    )
    monkeypatch.setattr(redis, "Redis", FakeRedis)
    result = tasks.geocode_pending_lots_task.run()
    assert "continuation" not in result


def test_beat_keeps_five_minute_watchdogs_but_publication_latency_is_bounded() -> None:
    schedule = tasks.celery_app.conf.beat_schedule
    assert schedule["geocode-pending-lots"]["schedule"] == 300.0
    assert schedule["publish-dirty-map-dataset"]["schedule"] == 300.0
    assert tasks._MAP_PUBLICATION_DEBOUNCE_SECONDS == 60


def test_heavy_tasks_use_isolated_queues() -> None:
    routes = tasks.celery_app.conf.task_routes
    assert routes["bankrotai.tasks.nationwide_lot_sync_task"]["queue"] == "ingestion"
    assert routes["bankrotai.tasks.geocode_pending_lots_task"]["queue"] == "geocoding"
    assert routes["bankrotai.tasks.build_map_dataset_task"]["queue"] == "map"
    assert tasks.celery_app.conf.task_default_queue == "maintenance"


def test_dirty_map_publication_is_coalesced(monkeypatch) -> None:
    import redis

    FakeRedis.values = {tasks._MAP_DIRTY_KEY: "1"}
    monkeypatch.setattr(redis, "Redis", FakeRedis)
    monkeypatch.setattr(tasks, "_schedule_map_dataset_build", lambda: {"status": "queued", "task_id": "map-1"})

    assert tasks.publish_dirty_map_dataset_task.run()["status"] == "queued"
    assert tasks.publish_dirty_map_dataset_task.run() == {"status": "skipped", "reason": "map-not-dirty"}


def test_automatic_cleanup_keeps_safe_retention_arguments(monkeypatch) -> None:
    captured: dict = {}

    def cleanup(_factory, **kwargs):
        captured.update(kwargs)
        return {"candidate_dataset_count": 0}

    monkeypatch.setattr(map_builder, "cleanup_map_datasets", cleanup)

    tasks.cleanup_old_map_datasets_task.run()

    assert captured == {"retain_previous_ready": 1, "min_age_hours": 24, "apply": True}
