"""Operator-only release orchestration stays strictly gated.

The script is parsed without execution on the Windows CI runner. These checks
prevent a future refactor from turning code merges into automatic production
writes or silently accepting skipped P1/P11.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts" / "launch-seven-source-gated.ps1").read_text(encoding="utf-8")
PHASE3 = (
    ROOT / ".github" / "workflows" / "phase3-lite-full-reconcile.yml"
).read_text(encoding="utf-8")


def test_launch_defaults_to_dry_run_and_checks_current_main_sha():
    assert "[switch]$Execute" in SCRIPT
    assert "if (-not $Execute)" in SCRIPT
    assert "DRY RUN ONLY; no workflows were dispatched." in SCRIPT
    assert "Assert-ExactMain" in SCRIPT
    assert "Assert-LocalRevision" in SCRIPT
    assert "git -C $root rev-parse HEAD" in SCRIPT
    assert "git -C $root status --porcelain" in SCRIPT
    assert "Local operator checkout is not the exact released main SHA" in SCRIPT
    assert "repos/$repo/branches/main" in SCRIPT
    assert "$current -cne $ExpectedSha" in SCRIPT
    assert "Test-Path" not in SCRIPT or "GH_TOKEN" not in SCRIPT


def test_all_acceptance_and_full_run_dispatches_are_after_explicit_opt_in():
    actual = SCRIPT.index("if (-not $Execute)")
    for marker in (
        "$p1Id = Start-TrackedWorkflow",
        "$p11Id = Start-TrackedWorkflow",
        "$fullId = Start-TrackedWorkflow",
    ):
        assert actual < SCRIPT.index(marker)
    assert SCRIPT.index("Wait-ForAcceptance -RunId $p1Id") < SCRIPT.index("$p11Id =")
    assert SCRIPT.index("Wait-ForAcceptance -RunId $p11Id") < SCRIPT.index("$fullId =")
    assert "'restart_drill=false'" in SCRIPT
    assert "Assert-NoActiveRun $Workflow" in SCRIPT
    assert "Refusing overlapping GitHub workflow" in SCRIPT
    assert "Ambiguous concurrent dispatches" in SCRIPT
    assert "Acceptance SHA mismatch" in SCRIPT
    assert "refusing full ingestion" in SCRIPT


def test_workflow_fails_closed_without_true_manual_p1_and_p11():
    job = PHASE3.split("  wait-home-deploy:", 1)[1].split("  reconcile:", 1)[0]
    assert "github.event_name == 'workflow_dispatch'" in job
    assert "github.ref" not in job or 'refs/heads/main' in job
    assert 'if [ "${GITHUB_REF}" != "refs/heads/main" ]; then' in job
    assert 'p1-data-quality.yml p11-production-acceptance.yml' in job
    assert '--event workflow_dispatch --limit 50' in job
    assert 'row.get("headSha")==expected' in job
    assert 'row.get("conclusion")=="success"' in job
    assert 'row.get("status")=="completed"' in job


def test_windows_ci_parses_launch_script():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "Parser]::ParseFile" in ci
    assert "scripts/launch-seven-source-gated.ps1" in ci
    assert "Gated release PowerShell parser rejected the script" in ci
