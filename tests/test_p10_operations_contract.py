from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_p10_host_snapshot_scripts_never_prune_volumes() -> None:
    paths = [
        ROOT / "scripts" / "p2-production-maintenance.ps1",
        ROOT / "scripts" / "backup-home-postgres.ps1",
        ROOT / "scripts" / "home-runner-network-diagnostics.ps1",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths).casefold()

    assert "record_operations_snapshot" in combined
    assert "docker volume prune" not in combined


def test_p10_runner_workflow_persists_status_without_checkout_dependency() -> None:
    text = (ROOT / ".github" / "workflows" / "home-runner-network-diagnostics.yml").read_text(encoding="utf-8")

    assert "record_operations_snapshot" in text
    assert "actions/checkout" not in text
    assert "bankrotai-home-map-worker" in text

def test_p10_backup_snapshot_uses_canonical_d_drive_and_three_verified_generations() -> None:
    text = (ROOT / "scripts" / "backup-home-postgres.ps1").read_text(encoding="utf-8")

    assert "Destination = 'D:\\BankrotAI\\dr-backups'" in text
    assert "RetainCount = 3" in text
    assert "restoreStatus -ne 'passed'" in text
    assert "Retention skipped because the new backup has not passed isolated restore verification." in text



def test_p10_snapshot_persistence_is_non_fatal_under_strict_powershell() -> None:
    paths = [
        ROOT / "scripts" / "p2-production-maintenance.ps1",
        ROOT / "scripts" / "backup-home-postgres.ps1",
        ROOT / "scripts" / "home-runner-network-diagnostics.ps1",
        ROOT / ".github" / "workflows" / "home-runner-network-diagnostics.yml",
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "$persistExitCode" in text
        assert "$previousErrorActionPreference" in text
        assert "$ErrorActionPreference = 'Continue'" in text
        assert "decode('utf-8-sig')" in text
        assert "$global:LASTEXITCODE = 0" in text
        assert "2>&1" in text
        assert "python -c $persistCommand *> $null" not in text
