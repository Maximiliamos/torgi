from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def test_release_requires_exact_sha_core_acceptance() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    for marker in (
        '"Production reliability"',
        '"Deploy home secondary origin"',
        '"P1 production data quality"',
        '"P11 production acceptance"',
    ):
        assert marker in text
    assert 'exact_required = {' in text
    assert 'head_sha=' in text


def test_release_allows_only_proven_nondeploy_ancestor_for_public_edge() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "/compare/" in text
    assert '"Dockerfile"' in text
    assert '"requirements.lock"' in text
    assert '"pyproject.toml"' in text
    assert '"alembic/"' in text
    assert '"src/"' in text
    assert '"WEB/"' in text
    assert '".github/workflows/regru-deploy.yml"' in text
    assert '".github/workflows/cloudflare-edge-deploy.yml"' in text
    assert "No accepted ancestor public deploy exists without intervening deploy-sensitive changes" in text
    assert "Public WEB smoke is not green for ancestor deploy" in text
    assert "Functional reliability is not green for ancestor deploy" in text
    assert "PUBLIC_DEPLOY_SHA=" in text
    assert "PUBLIC_DEPLOY_MODE=" in text
