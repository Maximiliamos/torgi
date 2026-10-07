from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "public-web-smoke.yml"


def test_scheduled_public_smoke_checks_latest_successful_edge_release() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "actions: read" in text
    assert "cloudflare-edge-deploy.yml/runs?branch=main&status=success&per_page=1" in text
    assert "TRIGGER_DEPLOY_SHA" in text
    assert 'if test "$GITHUB_EVENT_NAME" = "workflow_run"' in text
    assert 'test "$DEPLOYED_SHA" = "$EXPECTED_SHA"' in text
    assert "git ls-remote origin refs/heads/main" not in text
