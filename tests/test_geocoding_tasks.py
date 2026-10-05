from __future__ import annotations

from bankrotai import tasks
from bankrotai.services import geo_backfill, map_builder


class FakeRedis:
    values: dict[str, str] = {}

    @classmethod
    def from_url(cls, *_args, **_kwargs):
        return cls()

    def set(self, key: str, value: str) -> None:
        self.values[key] = str(value)

    def setnx(self, key: str, value: str) -> bool:
        if key in self.values:
            return False
        self.values[key] = str(value)
        return True

    def get(self, key: str):
        return self.values.get(key)

    def getdel(self, key: str):
        return self.values.pop(key, None)

    def pipeline(self, transaction: bool = True):
        del transaction
        return self

    def execute(self):
        return []

    def eval(self, _script: str, _numkeys: int, *keys: str):
        values = [self.values.get(key) for key in keys]
        for key in keys:
            self.values.pop(key, None)
        return values

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
        "_schedule_geocode_continuation",
        lambda depth: {
            "status": "queued",
            "task_id": "geo-next",
            "countdown_seconds": 2,
            "continuation_depth": depth,
        },
    )

    result = tasks.geocode_pending_lots_task.run()

    assert captured["limit"] == tasks._GEO_BATCH_LIMIT == 500
    assert captured["progress_task_id"].startswith("celery-")
    assert result["map_dataset_build"]["status"] == "dirty"
    assert result["map_dataset_build"]["quiet_seconds"] == tasks._MAP_PUBLICATION_QUIET_SECONDS
    assert result["map_dataset_build"]["maximum_delay_seconds"] == tasks._MAP_PUBLICATION_MAX_DELAY_SECONDS
    assert result["continuation"]["status"] == "queued"
    assert FakeRedis.values[tasks._MAP_DIRTY_KEY] == "1"


def test_geocoding_continuation_is_bounded(monkeypatch) -> None:
    import redis

    monkeypatch.setattr(
        geo_backfill,
        "geocode_pending_lots",
        lambda *_args, **_kwargs: {
            "queued": tasks._GEO_BATCH_LIMIT,
            "processed": tasks._GEO_BATCH_LIMIT,
            "geocoded": 0,
        },
    )
    monkeypatch.setattr(redis, "Redis", FakeRedis)

    result = tasks.geocode_pending_lots_task.run(
        continuation_depth=tasks._GEO_CONTINUATION_MAX_BATCHES - 1
    )

    assert result["continuation"] == {
        "status": "bounded_stop",
        "completed_batches": tasks._GEO_CONTINUATION_MAX_BATCHES,
        "max_batches": tasks._GEO_CONTINUATION_MAX_BATCHES,
        "resume": "celery-beat",
    }


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


def test_beat_keeps_geo_watchdogs_and_minute_map_publication_watchdog() -> None:
    schedule = tasks.celery_app.conf.beat_schedule
    assert schedule["geocode-pending-lots"]["schedule"] == 300.0
    assert schedule["recover-ik12-cadastral-misses"]["schedule"] == 300.0
    map_schedule = schedule["publish-dirty-map-dataset"]["schedule"]
    assert map_schedule.minute == set(range(60))
    assert tasks._MAP_PUBLICATION_QUIET_SECONDS == 180
    assert tasks._MAP_PUBLICATION_MIN_INTERVAL_SECONDS == 600
    assert tasks._MAP_PUBLICATION_MAX_DELAY_SECONDS == 900
    assert tasks._GEO_CONTINUATION_MAX_BATCHES == 8
    fast_schedule = schedule["refresh-nationwide-sources-fast"]["schedule"]
    full_schedule = schedule["refresh-nationwide-sources-full"]["schedule"]
    assert fast_schedule.minute == {0, 15, 30, 45}
    assert schedule["refresh-nationwide-sources-fast"]["args"] == ("fast",)
    assert schedule["refresh-nationwide-sources-fast"]["options"]["expires"] == 840
    assert full_schedule.hour == {3}
    assert full_schedule.minute == {7}
    assert schedule["refresh-nationwide-sources-full"]["args"] == ("full",)


def test_heavy_tasks_use_isolated_queues() -> None:
    routes = tasks.celery_app.conf.task_routes
    assert routes["bankrotai.tasks.nationwide_lot_sync_task"]["queue"] == "ingestion"
    assert routes["bankrotai.tasks.geocode_pending_lots_task"]["queue"] == "geocoding"
    assert routes["bankrotai.tasks.recover_ik12_geo_task"]["queue"] == "geocoding"
    assert routes["bankrotai.tasks.build_map_dataset_task"]["queue"] == "map"
    assert tasks.celery_app.conf.task_default_queue == "maintenance"


def test_dirty_map_publication_is_coalesced(monkeypatch) -> None:
    import redis

    class EmptySession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def scalar(self, *_args, **_kwargs):
            return None

    FakeRedis.values = {
        tasks._MAP_DIRTY_KEY: "1",
        tasks._MAP_DIRTY_SINCE_KEY: "1",
        tasks._MAP_DIRTY_LAST_CHANGE_KEY: "1",
    }
    monkeypatch.setattr(redis, "Redis", FakeRedis)
    monkeypatch.setattr(tasks, "SessionLocal", lambda: EmptySession())
    monkeypatch.setattr(tasks.time, "time", lambda: 10_000)
    monkeypatch.setattr(tasks, "_schedule_map_dataset_build", lambda: {"status": "queued", "task_id": "map-1"})

    assert tasks.publish_dirty_map_dataset_task.run()["status"] == "queued"
    assert tasks.publish_dirty_map_dataset_task.run() == {"status": "skipped", "reason": "map-not-dirty"}


def test_automatic_cleanup_keeps_safe_retention_arguments(monkeypatch) -> None:
    calls: list[dict] = []

    def cleanup(_factory, **kwargs):
        calls.append(dict(kwargs))
        return {
            "candidate_dataset_count": 1,
            "candidate_versions": ["old-r6-bundle-s3"],
        }

    deleted: list[list[str]] = []
    monkeypatch.setattr(map_builder, "cleanup_map_datasets", cleanup)
    monkeypatch.setattr(
        "bankrotai.services.map_object_store.delete_retired_dataset_manifests",
        lambda versions: deleted.append(list(versions)) or {"status": "deleted", "deleted": len(versions)},
    )

    result = tasks.cleanup_old_map_datasets_task.run()

    assert calls == [
        {
            "retain_previous_ready": 2,
            "min_age_hours": 1,
            "building_min_age_hours": 6,
            "apply": False,
        },
        {
            "retain_previous_ready": 2,
            "min_age_hours": 1,
            "building_min_age_hours": 6,
            "apply": True,
        },
    ]
    assert deleted == [["old-r6-bundle-s3"]]
    assert result["manifest_retention"]["status"] == "deleted"


def test_ik12_recovery_marks_map_dirty_only_when_it_recovers(monkeypatch) -> None:
    import redis

    FakeRedis.values = {}
    monkeypatch.setattr(
        geo_backfill,
        "run_ik12_recovery_batch",
        lambda *_args, **_kwargs: {
            "status": "completed",
            "queued": 5,
            "processed": 5,
            "recovered": 2,
            "failed": 3,
        },
    )
    monkeypatch.setattr(redis, "Redis", FakeRedis)
    result = tasks.recover_ik12_geo_task.run()

    assert result["recovered"] == 2
    assert result["map_dataset_build"]["status"] == "dirty"
    assert result["map_dataset_build"]["quiet_seconds"] == tasks._MAP_PUBLICATION_QUIET_SECONDS
    assert result["map_dataset_build"]["maximum_delay_seconds"] == tasks._MAP_PUBLICATION_MAX_DELAY_SECONDS
    assert FakeRedis.values[tasks._MAP_DIRTY_KEY] == "1"
