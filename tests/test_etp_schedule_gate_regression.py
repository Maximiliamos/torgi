"""Regression gate for the deployed 09:00/21:00 ETP production contract.

These checks must agree with the Home diagnostic command, while preserving
non-reconciliation and exact-source limits. No live source or database I/O.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_scheduled_nistp_detail_cap_matches_deployed_readonly_gate() -> None:
    from bankrotai.services.ingestion import default_source_specs
    from bankrotai.tasks import celery_app

    specs = {spec.source_id: spec for spec in default_source_specs()}
    wanted = {"m-ets.ru", "cdtrf.ru", "nistp.ru"}
    assert wanted <= set(specs)
    assert "alfalot.ru" not in specs
    assert all(
        specs[source].reconcile_missing is False
        and specs[source].max_batches == 500
        and specs[source].require_source_end is True
        for source in wanted
    )
    assert specs["nistp.ru"].max_detail_attempts_per_page == 2
    assert specs["m-ets.ru"].max_detail_attempts_per_page is None
    assert specs["cdtrf.ru"].max_detail_attempts_per_page is None
    assert specs["m-ets.ru"].allow_checkpoint_resume is True
    assert specs["cdtrf.ru"].allow_checkpoint_resume is True
    assert specs["bidexpert.ru"].filters.category == "realty"

    from bankrotai.services.etp_mets_full_catalogue import (
        OMITTED_PROPERTY_CATEGORY_IDS,
        SELECTED_CATEGORY_IDS,
    )
    assert SELECTED_CATEGORY_IDS == ("36", "34", "39", "37", "38", "28", "41")
    assert {"11", "40"} <= set(OMITTED_PROPERTY_CATEGORY_IDS)

    beat = celery_app.conf.beat_schedule["refresh-nationwide-sources-full"]
    assert beat["args"] == ("incremental",)
    assert set(beat["schedule"].hour) == {6, 18}
    assert set(beat["schedule"].minute) == {0}


def test_home_checklist_preserves_failure_and_prints_native_diagnostics() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "etp-parser-stage-checklist.yml"
    ).read_text(encoding="utf-8")
    verify = workflow.split(
        "- name: Verify deployed 09:00/21:00 ETP schedule and NISTP safety caps"
    )[1].split("- name: Diagnose all 22 ETP sources", 1)[0]
    assert "max_detail_attempts_per_page == 2" in verify
    assert "$previousPreference = $ErrorActionPreference" in verify
    assert "$ErrorActionPreference = 'Continue'" in verify
    assert "$ErrorActionPreference = $previousPreference" in verify
    assert "$status = $LASTEXITCODE" in verify
    assert "if ($status -ne 0" in verify
    assert "throw 'Home worker has no verified bounded" in verify


def test_p1_p11_gate_is_not_silently_disabled_or_bypassed_by_fix() -> None:
    p1 = (ROOT / ".github" / "workflows" / "p1-data-quality.yml").read_text()
    p11 = (ROOT / ".github" / "workflows" / "p11-production-acceptance.yml").read_text()
    preflight = (ROOT / "scripts" / "etp-canary-readiness.ps1").read_text()
    assert "github.event_name == 'workflow_dispatch'" in p1
    assert "github.event_name == 'workflow_dispatch'" in p11
    assert "verify-etp-canary-acceptance.ps1" in preflight
    assert "restore_verification -ne 'passed'" in preflight


def test_incremental_preflight_failure_prevents_nationwide_run_creation(monkeypatch) -> None:
    import bankrotai.tasks as tasks
    import bankrotai.services.scheduled_ingestion_preflight as preflight

    monkeypatch.setattr(tasks, "_global_source_network_blocked", lambda: False)
    specs = tuple(
        SimpleNamespace(source_id=source)
        for source in preflight.SCHEDULED_SOURCE_IDS
    )
    monkeypatch.setattr(tasks, "default_source_specs", lambda: specs)
    monkeypatch.setattr(tasks, "_unpaused_source_specs", lambda rows: rows)
    monkeypatch.setattr(
        preflight,
        "run_scheduled_source_preflight",
        lambda specs: {
            "passed": False,
            "checked": 7,
            "failures": [{"source_system": "m-ets.ru"}],
            "results": [],
            "database_writes": 0,
        },
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("heavy nationwide run must not start after failed preflight")

    monkeypatch.setattr(tasks, "_run_automatic_nationwide_refresh", forbidden)

    result = tasks.automatic_nationwide_lot_refresh_task.run("incremental")

    assert result["status"] == "skipped"
    assert result["reason"] == "scheduled_source_preflight_failed"
    assert result["preflight"]["database_writes"] == 0


def test_incremental_preflight_passes_into_bounded_nationwide_run(monkeypatch) -> None:
    import bankrotai.tasks as tasks
    import bankrotai.services.scheduled_ingestion_preflight as preflight

    monkeypatch.setattr(tasks, "_global_source_network_blocked", lambda: False)
    specs = tuple(
        SimpleNamespace(source_id=source)
        for source in preflight.SCHEDULED_SOURCE_IDS
    )
    monkeypatch.setattr(tasks, "default_source_specs", lambda: specs)
    monkeypatch.setattr(tasks, "_unpaused_source_specs", lambda rows: rows)
    monkeypatch.setattr(
        preflight,
        "run_scheduled_source_preflight",
        lambda specs: {
            "passed": True,
            "checked": 2,
            "failures": [],
            "results": [],
            "database_writes": 0,
        },
    )
    captured = {}

    def run(task, **kwargs):
        captured.update(kwargs)
        return {"status": "success", "run_id": "offline"}

    monkeypatch.setattr(tasks, "_run_automatic_nationwide_refresh", run)

    result = tasks.automatic_nationwide_lot_refresh_task.run("incremental")

    assert result["status"] == "success"
    assert captured["mode"] == "incremental"
    assert captured["trigger_type"] == "scheduled_incr"
    assert captured["total_sources"] == 7
    assert captured["retry_if_busy"] is True


def test_incremental_schedule_refuses_six_of_seven_sources(monkeypatch) -> None:
    import bankrotai.tasks as tasks
    import bankrotai.services.scheduled_ingestion_preflight as preflight

    monkeypatch.setattr(tasks, "_global_source_network_blocked", lambda: False)
    specs = tuple(
        SimpleNamespace(source_id=source)
        for source in preflight.SCHEDULED_SOURCE_IDS[:-1]
    )
    monkeypatch.setattr(tasks, "default_source_specs", lambda: specs)
    monkeypatch.setattr(tasks, "_unpaused_source_specs", lambda rows: rows)

    def forbidden(*args, **kwargs):
        raise AssertionError("six-source scheduled run must not start")

    monkeypatch.setattr(tasks, "_run_automatic_nationwide_refresh", forbidden)

    result = tasks.automatic_nationwide_lot_refresh_task.run("incremental")

    assert result["status"] == "skipped"
    assert result["reason"] == "scheduled_source_set_incomplete"
    assert result["missing_sources"] == ["nistp.ru"]
    assert result["database_writes"] == 0
