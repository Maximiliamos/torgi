from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "public-web-smoke.yml"

# Production smoke provenance must never follow pull-request edge diagnostics.


def test_scheduled_public_smoke_checks_latest_successful_edge_release() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "actions: read" in text
    assert "cloudflare-edge-deploy.yml/runs?branch=main&status=success&per_page=1" in text
    assert "TRIGGER_DEPLOY_SHA" in text
    assert 'if test "$GITHUB_EVENT_NAME" = "workflow_run"' in text
    assert 'test "$DEPLOYED_SHA" = "$EXPECTED_SHA"' in text
    assert "git ls-remote origin refs/heads/main" not in text



def test_workflow_run_public_smoke_ignores_pr_edge_diagnostics() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "github.event.workflow_run.conclusion == 'success'" in text
    assert "github.event.workflow_run.event == 'push'" in text
    assert "github.event.workflow_run.head_branch == 'main'" in text
