from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGRU_WORKFLOW = ROOT / ".github" / "workflows" / "regru-deploy.yml"
CLOUDFLARE_WORKFLOW = ROOT / ".github" / "workflows" / "cloudflare-edge-deploy.yml"


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
