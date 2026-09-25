from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGRU_WORKFLOW = ROOT / ".github" / "workflows" / "regru-deploy.yml"
CLOUDFLARE_WORKFLOW = ROOT / ".github" / "workflows" / "cloudflare-edge-deploy.yml"
HOME_WORKFLOW = ROOT / ".github" / "workflows" / "home-secondary-deploy.yml"


PRODUCTION_PATHS = (
    '"Dockerfile"',
    '"requirements.lock"',
    '"pyproject.toml"',
    '"alembic/**"',
    '"src/**"',
    '"WEB/**"',
    '"edge-proxy/**"',
    '"api-proxy/**"',
    '".github/workflows/regru-deploy.yml"',
    '".github/workflows/cloudflare-edge-deploy.yml"',
)


def test_public_production_deploys_share_the_same_push_scope() -> None:
    regru = REGRU_WORKFLOW.read_text(encoding="utf-8")
    cloudflare = CLOUDFLARE_WORKFLOW.read_text(encoding="utf-8")

    for path in PRODUCTION_PATHS:
        assert path in regru
        assert path in cloudflare

    # Home-only rollout fixes must not force a public WEB/edge redeploy. The
    # home workflow has its own production job and S3 publication gate.
    assert '".github/workflows/home-secondary-deploy.yml"' not in regru
    assert '".github/workflows/home-secondary-deploy.yml"' not in cloudflare


def test_cloudflare_wait_budget_covers_regru_deploy_window() -> None:
    cloudflare = CLOUDFLARE_WORKFLOW.read_text(encoding="utf-8")
    deploy = cloudflare.split("\n  deploy:\n", 1)[1]
    assert "timeout-minutes: 45" in deploy
    assert "for attempt in $(seq 1 360); do" in deploy
    assert 'if test "$attempt" = 360; then' in deploy



def test_staged_s3_readiness_gates_retry_transient_http_failures() -> None:
    for workflow_path in (REGRU_WORKFLOW, CLOUDFLARE_WORKFLOW):
        workflow = workflow_path.read_text(encoding="utf-8")
        assert "for login_attempt in $(seq 1 12); do" in workflow
        assert 'login_ok="yes"' in workflow
        assert "for dataset_attempt in $(seq 1 180); do" in workflow
        assert "api/map/datasets/current" in workflow
        assert "|| true)" in workflow
        assert 'DATASET_READY="no"' in workflow
        assert "2>/dev/null || printf 'no'" in workflow
        assert "staged REG.RU S3 dataset not ready on attempt %s/180; retrying" in workflow
        assert "bundle_manifest_url" in workflow


def test_home_rollout_rebuilds_map_when_pipeline_revision_changes() -> None:
    workflow = HOME_WORKFLOW.read_text(encoding="utf-8")
    assert "MAP_DATASET_REVISION" in workflow
    assert "$desiredRevision" in workflow
    assert '"*-$revision-bundle-s3"' in workflow
    assert "Test-CurrentLayout $current $desiredLayout $desiredRevision" in workflow
