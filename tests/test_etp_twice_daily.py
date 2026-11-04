"""Safety and timezone contracts for the scheduled 22-ETP source pool."""
from __future__ import annotations

import pytest

from bankrotai.connectors.etp_sources import PROFILES
from bankrotai.services import etp_twice_daily as policy


def test_inventory_includes_21_new_sources_and_existing_lot_online() -> None:
    sources = policy.scheduled_source_inventory()
    assert len(PROFILES) == 21
    assert sources["total_etps"] == 22
    assert sources["baseline"] == "lot-online.ru"
    assert len(sources["read_only_qa_sources"]) == 21
    assert len(set(policy.ALL_ETP_SOURCE_IDS)) == 22
    assert "rts-tender.ru" in sources["blocked_profiles"]
    assert "utender.ru" in sources["blocked_profiles"]
    assert sources["full_inventory_accepted_by_qa"] is False
    assert policy.ACCEPTED_ETP_FOR_AUTOMATIC_INGEST == frozenset()


def test_unaccepted_sources_never_join_production_sync() -> None:
    from bankrotai.services.ingestion import default_source_specs

    source_ids = [spec.source_id for spec in default_source_specs()]
    assert source_ids == [
        "torgi.gov.ru", "tbankrot.ru", "lot-online.ru",
        "torgi-russia.ru", "bidexpert.ru",
    ]
    assert not (set(source_ids) & {profile.source_id for profile in PROFILES})


@pytest.mark.parametrize("unapproved", ["rts-tender.ru", "utender.ru"])
def test_configured_blocked_providers_cannot_enter_auto_sync(monkeypatch, unapproved: str) -> None:
    monkeypatch.setattr(policy, "ACCEPTED_ETP_FOR_AUTOMATIC_INGEST", frozenset({unapproved}))
    with pytest.raises(ValueError, match="source is blocked"):
        policy.accepted_etp_source_specs()


def test_generic_without_individual_detail_enrichment_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(policy, "ACCEPTED_ETP_FOR_AUTOMATIC_INGEST", frozenset({"nistp.ru"}))
    with pytest.raises(ValueError, match="per-lot detail"):
        policy.accepted_etp_source_specs()


def test_approved_sources_have_strict_non_archiving_full_specs(monkeypatch) -> None:
    from bankrotai.services.ingestion import default_source_specs, fast_source_specs

    monkeypatch.setattr(policy, "ACCEPTED_ETP_FOR_AUTOMATIC_INGEST", frozenset({"ausib.ru"}))
    specs = default_source_specs()
    approved = [spec for spec in specs if spec.source_id == "ausib.ru"]
    assert len(approved) == 1
    assert approved[0].reconcile_missing is False
    assert approved[0].require_source_end is True
    assert approved[0].max_batches == 3500
    # Fast one-page runs must not overwrite independently verified detailed lots.
    assert "ausib.ru" not in {
        spec.source_id for spec in fast_source_specs(gis_publish_date_from="2026-11-03")
    }


def test_0900_2100_moscow_beat_runs_existing_full_and_etp_qa() -> None:
    from bankrotai.tasks import celery_app

    schedules = celery_app.conf.beat_schedule
    full = schedules["refresh-nationwide-sources-full"]
    qa = schedules["audit-all-direct-etp-profiles"]
    assert full["task"] == "bankrotai.tasks.automatic_nationwide_lot_refresh_task"
    assert full["args"] == ("full",)
    assert qa["task"] == "bankrotai.tasks.scheduled_etp_readonly_audit_task"
    for entry in (full, qa):
        assert entry["schedule"].hour == {6, 18}
        assert entry["schedule"].minute == {0}
    assert schedules["geocode-pending-lots"]["schedule"] == 300.0
    assert schedules["publish-dirty-map-dataset"]["schedule"].minute == set(range(60))


def test_21_provider_schedule_is_readonly_and_does_not_fake_22_full_scans(monkeypatch) -> None:
    from bankrotai.services import etp_qa_batch
    from bankrotai.tasks import scheduled_etp_readonly_audit_task

    calls = []

    async def fake_run_all(*, pages: int, per_page: int, geo_limit: int) -> dict:
        calls.append((pages, per_page, geo_limit))
        return {
            "source_count": 21,
            "sources": [{"source": profile.source_id, "status": "preview"}
                        for profile in PROFILES],
            "qa_only": True, "database_writes": 0,
            "production_map_writes": 0, "archival_enabled": False,
            "complete_source_inventory": False,
        }

    monkeypatch.setattr(etp_qa_batch, "run_all", fake_run_all)
    result = scheduled_etp_readonly_audit_task.run()
    assert calls == [(2, 3, 0)]
    assert result["source_count"] == 21
    assert result["all_etp_source_count"] == 22
    assert result["full_catalogue_scans_completed"] == 0
    assert result["lot_online"]["evaluated_by_this_preview"] is False
    assert result["production_ingest_approvals"] == ()


def test_scheduled_audit_fails_if_any_source_result_missing(monkeypatch) -> None:
    from bankrotai.services import etp_qa_batch
    from bankrotai.tasks import scheduled_etp_readonly_audit_task

    async def bad_report(*, pages: int, per_page: int, geo_limit: int) -> dict:
        return {
            "source_count": 21,
            "sources": [{"source": PROFILES[0].source_id}],
            "qa_only": True, "database_writes": 0,
            "production_map_writes": 0, "archival_enabled": False,
            "complete_source_inventory": False,
        }

    monkeypatch.setattr(etp_qa_batch, "run_all", bad_report)
    with pytest.raises(RuntimeError, match="read-only QA contract"):
        scheduled_etp_readonly_audit_task.run()


@pytest.mark.parametrize("build_fails", [True, False])
def test_failed_map_publication_rearms_dirty_signal(monkeypatch, build_fails: bool) -> None:
    """A failed background build must not silently drop its consumed dirty key."""
    import redis
    from bankrotai import tasks
    from bankrotai.services import map_builder

    class FakeLock:
        def acquire(self, blocking=False):
            return True

        def release(self):
            return None

    class FakeRedis:
        values: dict[str, str] = {}

        @classmethod
        def from_url(cls, *_args, **_kwargs):
            return cls()

        def lock(self, *_args, **_kwargs):
            return FakeLock()

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value):
            self.values[key] = str(value)

        def setnx(self, key, value):
            return self.values.setdefault(key, str(value)) == str(value)

        def pipeline(self, **_kwargs):
            return self

        def execute(self):
            return []

        def close(self):
            pass

    class EmptySession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def scalar(self, *_args):
            return None

    def fake_build(_factory):
        if build_fails:
            raise RuntimeError("simulated S3 network failure")
        return {"status": "rejected", "promotion_status": "rejected"}

    monkeypatch.setattr(redis, "Redis", FakeRedis)
    monkeypatch.setattr(tasks, "SessionLocal", lambda: EmptySession())
    monkeypatch.setattr(map_builder, "map_source_fingerprint", lambda _session: "source-hash")
    monkeypatch.setattr(map_builder, "build_map_dataset", fake_build)
    if build_fails:
        with pytest.raises(RuntimeError, match="S3 network failure"):
            tasks.build_map_dataset_task.run()
    else:
        assert tasks.build_map_dataset_task.run()["status"] == "rejected"
    assert FakeRedis.values[tasks._MAP_DIRTY_KEY] == "1"
