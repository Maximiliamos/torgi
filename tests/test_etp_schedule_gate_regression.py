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

    beat = celery_app.conf.beat_schedule["refresh-nationwide-sources-full"]
    assert beat["args"] == ("full",)
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
