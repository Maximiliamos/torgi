from __future__ import annotations

from pathlib import Path


def test_all_source_campaign_has_every_profile_and_only_blocks_evidenced_sources():
    from bankrotai.services import etp_all_sources_campaign as campaign

    assert len(campaign.ALL_SOURCE_IDS) == 21
    assert len(campaign.RUNNABLE) == 19
    assert set(campaign.BLOCKED) == {
        "rts-tender.ru", "utender.ru",
    }
    assert "cdtrf.ru" in campaign.RUNNABLE
    assert "nistp.ru" in campaign.RUNNABLE


def test_workflow_passes_the_reviewed_runnable_allowlist_into_container():
    from bankrotai.services import etp_all_sources_campaign as campaign

    workflow = Path(".github/workflows/etp-all-new-sources-import-geo-map.yml").read_text(
        encoding="utf-8"
    )
    expected = ",".join(campaign.RUNNABLE)
    assert f"ETP_ACCEPTED_SOURCE_IDS: {expected}" in workflow
    assert "docker.exe exec -e ETP_ACCEPTED_SOURCE_IDS " in workflow


def test_no_vpn_operator_runs_full_campaign_with_progress_and_log():
    script = Path("scripts/run-all-etp-no-vpn-map.ps1").read_text(encoding="utf-8")
    assert "p1-data-quality.yml" in script
    assert "etp-all-new-sources-import-geo-map.yml" in script
    assert "etp-verified-canary-geo-map.yml" not in script
    assert "bankrotai.services.etp_all_sources_campaign progress" in script
    assert "Start-Transcript" in script
    assert "accepted_sha=$ExpectedSha" in script


def test_all_source_campaign_can_resume_at_geo_without_reimporting():
    workflow = Path(".github/workflows/etp-all-new-sources-import-geo-map.yml").read_text(
        encoding="utf-8"
    )
    script = Path("scripts/run-all-etp-no-vpn-map.ps1").read_text(encoding="utf-8")

    assert "resume_after_import:" in workflow
    assert "if: ${{ !inputs.resume_after_import }}" in workflow
    assert "[switch]$ResumeAfterImport" in script
    assert "resume_after_import=true" in script


def test_all_source_specs_are_bounded_non_reconciling(monkeypatch):
    from bankrotai.services import etp_all_sources_campaign as campaign
    import bankrotai.connectors.registry.cdtrf_sync as cdtrf

    monkeypatch.setenv("CDTRF_OPERATOR_SYNC_ENABLED", "1")
    monkeypatch.setenv("ETP_CANARY_BACKUP_VERIFIED", "1")
    monkeypatch.setenv("ETP_OPERATOR_CANARY_IMPORT_ENABLED", "1")
    specs = campaign._specs()

    assert tuple(spec.source_id for spec in specs) == campaign.RUNNABLE
    assert all(spec.reconcile_missing is False for spec in specs)
    assert all(spec.require_source_end is True for spec in specs)
    assert all(
        spec.max_batches <= 3500
        for spec in specs
        if spec.source_id != "cdtrf.ru" and spec.max_batches is not None
    )
    assert isinstance(next(spec for spec in specs if spec.source_id == "cdtrf.ru").filters,
                      cdtrf.CdtrfSyncFilters)
    assert cdtrf.operator_sync_spec().require_source_end is True


def test_all_source_campaign_requires_all_write_guards(monkeypatch):
    import pytest
    from bankrotai.services import etp_all_sources_campaign as campaign

    for name in (
        "ETP_ALL_SOURCES_OPERATOR_ENABLED", "ETP_CANARY_BACKUP_VERIFIED",
        "ETP_OPERATOR_CANARY_IMPORT_ENABLED", "CDTRF_OPERATOR_SYNC_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(PermissionError, match="source acceptance missing"):
        campaign._authorized()
    monkeypatch.setenv("ETP_ACCEPTED_SOURCE_IDS", ",".join(campaign.RUNNABLE))
    with pytest.raises(PermissionError, match="authorization missing"):
        campaign._authorized()


def test_all_source_campaign_journal_fields_fit_production_schema(monkeypatch):
    from bankrotai.db import LotSyncRun
    from bankrotai.services import etp_all_sources_campaign as campaign

    assert len(campaign.RUN_TRIGGER_TYPE) <= LotSyncRun.__table__.c.trigger_type.type.length
    assert len(campaign.RUN_TRIGGERED_BY) <= LotSyncRun.__table__.c.triggered_by.type.length

    class StopAfterCreate(Exception):
        pass

    captured = {}

    class FakeService:
        def __init__(self, *args, **kwargs):
            pass

        def create_run(self, **kwargs):
            captured.update(kwargs)
            raise StopAfterCreate

    for flag in (
        "ETP_ALL_SOURCES_OPERATOR_ENABLED",
        "ETP_CANARY_BACKUP_VERIFIED",
        "ETP_OPERATOR_CANARY_IMPORT_ENABLED",
        "CDTRF_OPERATOR_SYNC_ENABLED",
    ):
        monkeypatch.setenv(flag, "1")
    monkeypatch.setenv("ETP_ACCEPTED_SOURCE_IDS", ",".join(campaign.RUNNABLE))
    # This test only checks the journal schema after the authorization gate.
    # Production does not have these independent source approvals.
    from bankrotai.services import etp_twice_daily
    monkeypatch.setattr(
        etp_twice_daily, "ACCEPTED_ETP_FOR_AUTOMATIC_INGEST",
        frozenset(campaign.RUNNABLE),
    )
    monkeypatch.setattr(campaign, "SequentialCampaignService", FakeService)

    import pytest
    with pytest.raises(StopAfterCreate):
        campaign.run_import()

    assert captured["trigger_type"] == campaign.RUN_TRIGGER_TYPE
    assert captured["triggered_by"] == campaign.RUN_TRIGGERED_BY
    assert captured["total_sources"] == len(campaign.RUNNABLE)


def test_geocode_campaign_waits_for_the_shared_geo_lock(monkeypatch):
    from contextlib import contextmanager

    from bankrotai.services import etp_all_sources_campaign as campaign
    import bankrotai.services.geo_backfill as geo_backfill

    class ScalarResult:
        def all(self):
            return ["lot-1"]

    class Session:
        def scalars(self, _statement):
            return ScalarResult()

    @contextmanager
    def session_factory():
        yield Session()

    calls = []

    def geocode(*_args, **_kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise geo_backfill.GeoBatchAlreadyRunning("busy")
        return {"status": "completed", "processed": 1}

    from bankrotai.services import etp_twice_daily
    monkeypatch.setattr(
        etp_twice_daily, "ACCEPTED_ETP_FOR_AUTOMATIC_INGEST",
        frozenset(campaign.RUNNABLE),
    )
    monkeypatch.setattr(campaign, "SessionLocal", session_factory)
    monkeypatch.setattr(campaign, "snapshot", lambda: {})
    monkeypatch.setattr(campaign.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(geo_backfill, "geocode_pending_lots", geocode)

    result = campaign.geocode_all(batch_size=1)

    assert len(calls) == 2
    assert result["requested"] == 1
    assert result["batches"][0]["status"] == "completed"


def test_failed_all_source_campaign_reports_attempted_fraction_not_fake_100(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from bankrotai.services import etp_all_sources_campaign as campaign

    rows = [
        SimpleNamespace(
            status=status, source_system="example.ru", checkpoint_json={},
            pages_scanned=1, items_seen=1, items_inserted=1,
            items_updated=0, items_failed=0, error_message=None,
        )
        for status in ("success", "failed")
    ]

    class Session:
        def scalar(self, _statement):
            return SimpleNamespace(id="run-1", status="failed")

        def scalars(self, _statement):
            return SimpleNamespace(all=lambda: rows)

    @contextmanager
    def factory():
        yield Session()

    monkeypatch.setattr(campaign, "SessionLocal", factory)
    result = campaign.progress()
    assert result["status"] == "failed"
    assert result["completed"] == 2
    assert result["total"] == 19
    assert result["percent"] == 10
    assert result["succeeded"] == 1
    assert result["failed"] == 1


def test_source_snapshot_reports_missing_active_primary_prices(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from bankrotai.services import etp_all_sources_campaign as campaign

    class Session:
        def execute(self, _statement):
            return SimpleNamespace(all=lambda: [
                ("sistematorg.com", 3361, 18, 1184, 150, 18, 100),
            ])

    @contextmanager
    def factory():
        yield Session()

    monkeypatch.setattr(campaign, "SessionLocal", factory)
    result = campaign.snapshot()
    source = result["sources"]["sistematorg.com"]
    assert source["records"] == 3361
    assert source["active_primary"] == 150
    assert source["active_primary_missing_price"] == 132
    assert source["active_primary_with_geo"] == 100
    assert source["requires_source_acceptance"] is True
    assert result["sources"]["nistp.ru"]["records"] == 0


def test_operator_cannot_start_unapproved_all_source_campaign(monkeypatch):
    import pytest
    from bankrotai.services import etp_all_sources_campaign as campaign
    for flag in ("ETP_ALL_SOURCES_OPERATOR_ENABLED", "ETP_CANARY_BACKUP_VERIFIED",
                 "ETP_OPERATOR_CANARY_IMPORT_ENABLED", "CDTRF_OPERATOR_SYNC_ENABLED"):
        monkeypatch.setenv(flag, "1")
    monkeypatch.setenv("ETP_ACCEPTED_SOURCE_IDS", "cdtrf.ru,m-ets.ru")
    with pytest.raises(PermissionError, match="acceptance missing for"):
        campaign._authorized()



def test_all_source_workflow_env_cannot_override_source_acceptance(monkeypatch):
    """Reject the existing 19-source allowlist even with all operator flags."""
    import pytest
    from bankrotai.services import etp_all_sources_campaign as campaign
    from bankrotai.services import etp_twice_daily

    for name in (
        "ETP_ALL_SOURCES_OPERATOR_ENABLED", "ETP_CANARY_BACKUP_VERIFIED",
        "ETP_OPERATOR_CANARY_IMPORT_ENABLED", "CDTRF_OPERATOR_SYNC_ENABLED",
    ):
        monkeypatch.setenv(name, "1")
    monkeypatch.setenv("ETP_ACCEPTED_SOURCE_IDS", ",".join(campaign.RUNNABLE))
    assert not etp_twice_daily.ACCEPTED_ETP_FOR_AUTOMATIC_INGEST
    with pytest.raises(PermissionError, match="no independent acceptance"):
        campaign._authorized()



def test_resume_after_import_cannot_geocode_or_publish_unaccepted_sources():
    import pytest
    from bankrotai.services import etp_all_sources_campaign as campaign
    from bankrotai.services import etp_twice_daily

    assert not etp_twice_daily.ACCEPTED_ETP_FOR_AUTOMATIC_INGEST
    with pytest.raises(PermissionError, match="independently accepted"):
        campaign.geocode_all()
    with pytest.raises(PermissionError, match="independently accepted"):
        campaign.publish_map()
