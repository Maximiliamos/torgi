from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
PUBLIC_SMOKE = ROOT / ".github" / "workflows" / "public-web-smoke.yml"
PRODUCTION_FUNCTIONAL = ROOT / ".github" / "workflows" / "production-functional.yml"
OPERATIONS = ROOT / "docs" / "phase4-lite-operations.md"


def test_python_runtime_dependencies_are_audited() -> None:
    workflow = CI.read_text(encoding="utf-8")
    assert "name: Python dependency audit" in workflow
    assert "pip-audit==2.10.1" in workflow
    assert "pip-audit -r requirements.lock" in workflow


def test_public_web_alert_is_deduplicated_and_auto_recovers() -> None:
    workflow = PUBLIC_SMOKE.read_text(encoding="utf-8")
    assert "[Production] Public WEB smoke alert" in workflow
    assert "listForRepo" in workflow
    assert "issue.title === title" in workflow
    assert "state: 'closed'" in workflow
    assert "state_reason: 'completed'" in workflow
    assert "if: success() && github.ref == 'refs/heads/main'" in workflow


def test_functional_alert_is_deduplicated_and_auto_recovers() -> None:
    workflow = PRODUCTION_FUNCTIONAL.read_text(encoding="utf-8")
    assert "[Production] Functional reliability alert" in workflow
    assert "listForRepo" in workflow
    assert "issue.title === title" in workflow
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
