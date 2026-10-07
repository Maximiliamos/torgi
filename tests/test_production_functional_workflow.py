from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "production-functional.yml"


def test_functional_workflow_ignores_cloudflare_pr_diagnostics() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "github.event.workflow_run.event == 'push'" in text
    assert "TRIGGER_DEPLOY_SHA" in text
    assert "TRIGGER_DEPLOY_EVENT" in text


def test_scheduled_functional_gate_uses_latest_successful_push_edge_release() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "cloudflare-edge-deploy.yml/runs?branch=main&event=push&status=success&per_page=1" in text
    assert 'EXPECTED_SHA="$TRIGGER_DEPLOY_SHA"' in text
    assert "git ls-remote origin refs/heads/main" not in text
    assert 'test "$DEPLOYED_SHA" = "$EXPECTED_SHA"' in text
