from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "p11-production-acceptance.yml"


def test_p11_acceptance_is_main_only_deploy_gated_and_bounded() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "schedule:" not in text
    assert "branches: [main]" in text
    assert "'.github/workflows/p11-production-acceptance.yml'" in text
    assert "Wait for exact Home production revision" in text
    assert "Deploy home secondary origin" in text
    assert "head_sha=$GITHUB_SHA" in text
    assert "$minutes = if ($pushRun) { 30 }" in text
    assert text.count("$minutes = if ($pushRun) { 30 }") >= 2
    assert "P11 acceptance may run only from main" in text
    assert "soak_minutes must be between 0 and 60" in text
    assert "timeout-minutes: 150" in text


def test_p11_acceptance_collects_required_production_evidence() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    for marker in (
        "phase3-production-health.ps1",
        "restore_verification",
        "_verify_public_manifest",
        "source_global_network_state",
        "geocoding_progress",
        "home-runner-network-diagnostics.ps1",
        "p11-production-acceptance-",
    ):
        assert marker in text


def test_p11_restart_is_opt_in_and_never_prunes_volumes() -> None:
    text = WORKFLOW.read_text(encoding="utf-8").casefold()

    assert "restart_drill:" in text
    assert "default: false" in text
    assert "github.event_name == 'workflow_dispatch' && inputs.restart_drill" in text
    assert "docker volume prune" not in text
    assert "shutdown" not in text
    assert "restart-computer" not in text


def test_p11_acceptance_requires_current_d_drive_backup_policy() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "D:\\BankrotAI\\dr-backups" in text
    assert "48-hour policy" in text
    assert "$age -gt 60" in text
    assert "C:\\ProgramData\\BankrotAI\\dr-backups" not in text


def test_p11_acceptance_uses_canonical_photon_container_name() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "'bankrotai-photon'" in text
    assert "'bankrotai-home-photon'" not in text


def test_p11_acceptance_bounds_transient_network_wait() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert 'GeoFailure.status == "network_wait"' in text
    assert "max_allowed = max(25, min(100, math.ceil(max(actionable, 1) * 0.05)))" in text
    assert "stale_cutoff = now - timedelta(minutes=15)" in text
    assert "future_limit = now + timedelta(minutes=30)" in text
    assert '"network_wait exceeds bounded operational queue"' in text
    assert '"stale network_wait rows exceeded retry grace"' in text
    assert '"network_wait retry scheduled outside bounded recovery horizon"' in text
    assert "network_wait == 0" not in text
