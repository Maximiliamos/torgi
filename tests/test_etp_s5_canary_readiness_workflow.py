"""Ensure S5 evidence cannot silently become automatic production import."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/etp-s5-canary-readiness.yml").read_text(
    encoding="utf-8"
)
SCRIPT = (ROOT / "scripts/etp-canary-readiness.ps1").read_text(
    encoding="utf-8"
)


def test_preflight_only_after_exact_accepted_main():
    # P11, public WEB and functional smoke may finish in either order.
    # Every trigger keeps the *same* exact-SHA production gates.
    assert '- "P11 production acceptance"' in WORKFLOW
    assert '- "Public WEB smoke"' in WORKFLOW
    assert '- "Production functional reliability"' in WORKFLOW
    assert "github.event.workflow_run.head_sha != ''" in WORKFLOW
    assert "github.event.workflow_run.conclusion == 'success'" in WORKFLOW
    assert "github.event.workflow_run.head_branch == 'main'" in WORKFLOW
    assert "ref: ${{ github.event.workflow_run.head_sha }}" in WORKFLOW
    assert "-ExpectedSha '${{ github.event.workflow_run.head_sha }}'" in WORKFLOW
    assert "bankrotai-home" in WORKFLOW
    assert "if: always()" in WORKFLOW
    assert "etp-s5-canary-readiness" in WORKFLOW


def test_readonly_operator_source_proof_never_invokes_apply_or_db_write():
    assert "verify-etp-canary-acceptance.ps1" in SCRIPT
    assert SCRIPT.index("verify-etp-canary-acceptance.ps1") < SCRIPT.index(
        "$report.restore_verified_backup_available = Test-RecentRestoreVerifiedBackup"
    )
    assert SCRIPT.index("if ($report.restore_verified_backup_available -ne $true)") < SCRIPT.index(
        "docker.exe exec bankrotai-home-ingestion-worker python -m bankrotai.services.etp_manual_import"
    )
    lines = [line for line in SCRIPT.splitlines()
             if "docker.exe exec" in line and not line.lstrip().startswith("#")]
    assert len(lines) == 1
    assert "--limit 2" in lines[0]
    assert "--source $source" in lines[0]
    assert "--apply" not in lines[0]
    assert "production_database_writes = 0" in SCRIPT
    assert "import_executed = $false" in SCRIPT
    assert "ready_for_automatic_sync = $false" in SCRIPT
    assert "map_publication_verified = $false" in SCRIPT
    assert "reconcile_missing = $false" in SCRIPT
    assert "existing_cadastral_collision_checked = $false" in SCRIPT
    assert "existing_cadastral_collision_checked = $true" in SCRIPT
    assert "--audit-collisions" in lines[0]
    assert "$review.new_unconflicted_candidate_count -ne $candidate.verified_detail_count" in SCRIPT
    assert "$review.blocked_candidate_count -ne 0" in SCRIPT
    assert "$review.database_writes -ne 0" in SCRIPT
    assert "$review.revalidate_in_atomic_import -ne $true" in SCRIPT


def test_readiness_requires_real_backup_hash_and_partial_source_details():
    assert "restore_verification -ne 'passed'" in SCRIPT
    assert "source_schema_revision -ne $data.restored_schema_revision" in SCRIPT
    assert "Get-FileHash -Algorithm SHA256" in SCRIPT
    assert "$hours -gt 60" in SCRIPT
    assert "complete_source_inventory -ne $false" in SCRIPT
    assert "persisted_count -ne 0" in SCRIPT
    assert "verified_detail_count -lt 1" in SCRIPT
    assert "verified_detail_count -gt 2" in SCRIPT
    assert "blocked_source_details_unverified" in SCRIPT
    assert "blocking_reasons" in SCRIPT
    assert "manual_source_import_is_separate_action = $true" in SCRIPT
