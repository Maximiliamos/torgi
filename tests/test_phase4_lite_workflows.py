from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
PUBLIC_SMOKE = ROOT / ".github" / "workflows" / "public-web-smoke.yml"
PRODUCTION_FUNCTIONAL = ROOT / ".github" / "workflows" / "production-functional.yml"
OPERATIONS = ROOT / "docs" / "phase4-lite-operations.md"
P1_QUALITY = ROOT / ".github" / "workflows" / "p1-data-quality.yml"
P1_AUDIT = ROOT / "scripts" / "p1-production-audit.ps1"


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
        "55-minute soft / 60-minute hard",
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
