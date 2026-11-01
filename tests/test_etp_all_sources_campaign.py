from __future__ import annotations


def test_all_source_campaign_has_every_profile_and_only_blocks_evidenced_sources():
    from bankrotai.services import etp_all_sources_campaign as campaign

    assert len(campaign.ALL_SOURCE_IDS) == 21
    assert len(campaign.RUNNABLE) == 16
    assert set(campaign.BLOCKED) == {
        "rts-tender.ru", "etp-profit.ru", "ausib.ru", "utender.ru", "electro-torgi.ru",
    }
    assert "cdtrf.ru" in campaign.RUNNABLE
    assert "nistp.ru" in campaign.RUNNABLE


def test_all_source_specs_are_bounded_non_reconciling(monkeypatch):
    from bankrotai.services import etp_all_sources_campaign as campaign
    import bankrotai.connectors.registry.cdtrf_sync as cdtrf

    monkeypatch.setenv("CDTRF_OPERATOR_SYNC_ENABLED", "1")
    monkeypatch.setenv("ETP_CANARY_BACKUP_VERIFIED", "1")
    monkeypatch.setenv("ETP_OPERATOR_CANARY_IMPORT_ENABLED", "1")
    specs = campaign._specs()

    assert tuple(spec.source_id for spec in specs) == campaign.RUNNABLE
    assert all(spec.reconcile_missing is False for spec in specs)
    assert all(
        spec.max_batches <= 500
        for spec in specs
        if spec.source_id != "cdtrf.ru" and spec.max_batches is not None
    )
    assert isinstance(next(spec for spec in specs if spec.source_id == "cdtrf.ru").filters,
                      cdtrf.CdtrfSyncFilters)


def test_all_source_campaign_requires_all_write_guards(monkeypatch):
    import pytest
    from bankrotai.services import etp_all_sources_campaign as campaign

    for name in (
        "ETP_ALL_SOURCES_OPERATOR_ENABLED", "ETP_CANARY_BACKUP_VERIFIED",
        "ETP_OPERATOR_CANARY_IMPORT_ENABLED", "CDTRF_OPERATOR_SYNC_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
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

    monkeypatch.setattr(campaign, "SessionLocal", session_factory)
    monkeypatch.setattr(campaign, "snapshot", lambda: {})
    monkeypatch.setattr(campaign.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(geo_backfill, "geocode_pending_lots", geocode)

    result = campaign.geocode_all(batch_size=1)

    assert len(calls) == 2
    assert result["requested"] == 1
    assert result["batches"][0]["status"] == "completed"
