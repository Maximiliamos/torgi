from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
PUBLIC_SMOKE = ROOT / ".github" / "workflows" / "public-web-smoke.yml"
PRODUCTION_FUNCTIONAL = ROOT / ".github" / "workflows" / "production-functional.yml"
OPERATIONS = ROOT / "docs" / "phase4-lite-operations.md"
P1_QUALITY = ROOT / ".github" / "workflows" / "p1-data-quality.yml"
P1_AUDIT = ROOT / "scripts" / "p1-production-audit.ps1"
P2_MAINTENANCE = ROOT / ".github" / "workflows" / "p2-production-maintenance.yml"
P2_SCRIPT = ROOT / "scripts" / "p2-production-maintenance.ps1"
HOME_DEPLOY = ROOT / ".github" / "workflows" / "home-secondary-deploy.yml"
BACKUP_WORKFLOW = ROOT / ".github" / "workflows" / "phase3-lite-backup.yml"


def test_python_runtime_dependencies_are_audited() -> None:
    workflow = CI.read_text(encoding="utf-8")
    assert "name: Python dependency audit" in workflow
    assert "pip-audit==2.10.1" in workflow
    assert "pip-audit -r requirements.lock" in workflow


def test_public_web_alert_is_deduplicated_and_auto_recovers() -> None:
    workflow = PUBLIC_SMOKE.read_text(encoding="utf-8")
    assert "[Production] Public WEB smoke alert" in workflow
    assert "github.paginate(github.rest.issues.listForRepo" in workflow
    assert "Public WEB smoke failed:" in workflow
    assert "item.title.startsWith(legacyPrefix)" in workflow
    assert "state: 'closed'" in workflow
    assert "state_reason: 'completed'" in workflow
    assert "if: success() && github.ref == 'refs/heads/main'" in workflow


def test_functional_alert_is_deduplicated_and_auto_recovers() -> None:
    workflow = PRODUCTION_FUNCTIONAL.read_text(encoding="utf-8")
    assert "[Production] Functional reliability alert" in workflow
    assert "github.paginate(github.rest.issues.listForRepo" in workflow
    assert "Production functional reliability failed:" in workflow
    assert "item.title.startsWith(legacyPrefix)" in workflow
    assert "state: 'closed'" in workflow
    assert "state_reason: 'completed'" in workflow
    assert "if: success() && github.ref == 'refs/heads/main'" in workflow


def test_phase4_operations_runbook_keeps_phase3_safety_contracts() -> None:
    runbook = OPERATIONS.read_text(encoding="utf-8")
    for text in (
        "exact main SHA",
        "Prefer application rollback over database restore",
        "Never use an unverified dump directly against the live database",
        "Never bypass the single-active ingestion lock",
        "4-hour soft / 5-hour hard",
        "no more than four application users",
    ):
        assert text in runbook


def test_p1_data_quality_reconciles_db_map_and_public_s3() -> None:
    workflow = P1_QUALITY.read_text(encoding="utf-8")
    script = P1_AUDIT.read_text(encoding="utf-8")

    assert "Phase 3 Lite full source reconciliation" in workflow
    assert "p1-production-audit.ps1" in workflow
    assert "p1-data-quality-" in workflow
    assert "[P1] Data quality / map delivery alert" in workflow
    assert "map_delivery_reconciliation_report(s, verify_public_manifest=True)" in script
    assert "geocoding_diagnostic_report(s)" in script
    assert "if (-not $result.healthy) { exit 1 }" in script


def test_p2_maintenance_is_bounded_and_fail_closed() -> None:
    workflow = P2_MAINTENANCE.read_text(encoding="utf-8")
    script = P2_SCRIPT.read_text(encoding="utf-8")
    deploy = HOME_DEPLOY.read_text(encoding="utf-8")
    backup = BACKUP_WORKFLOW.read_text(encoding="utf-8")

    assert "17 5 * * *" in workflow
    assert "Deploy home secondary origin" in workflow
    assert "p2-production-maintenance.ps1" in workflow
    assert "[P2] Production maintenance alert" in workflow
    assert "docker image prune --force" in script
    assert "docker builder prune --force" in script
    assert "docker system prune" not in script
    assert "disk-c-critical" in script
    assert "disk-c-headroom" in script
    assert "CriticalFreePercent = 10" in script
    assert "WarningFreePercent = 15" in script
    assert "cleanup_old_map_datasets_task" in script
    assert "--log-opt max-size=20m --log-opt max-file=5" in deploy
    assert "Docker log rotation is not enforced" in deploy
    assert "-RetainDays 14" in backup
    assert "actions: read" in backup
    assert "wait-home-deploy:" in backup
    assert "needs: wait-home-deploy" in backup
    assert "Wait for the same main revision on the home origin" in backup
    assert "Reclaim safe backup space under critical disk pressure" in backup
    assert "Backup refused: C: remains below 10% free after safe cleanup" in backup
    assert "restore_verification -eq 'passed'" in backup
    assert "preserving recovery anchors" in deploy
    assert "Select-Object -First 2" in deploy
    assert "restore_verification -eq 'passed'" in deploy
    assert "Get-FreePercent) -lt $CriticalFreePercent" in script
    assert "Select-Object -First 2" in script
