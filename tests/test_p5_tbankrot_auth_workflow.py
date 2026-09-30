from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOME_DEPLOY = ROOT / ".github" / "workflows" / "home-secondary-deploy.yml"
DOCKERFILE = ROOT / "Dockerfile"


def test_tbankrot_auth_center_runtime_is_writable_only_in_api() -> None:
    workflow = HOME_DEPLOY.read_text(encoding="utf-8")

    assert "TBANKROT_COOKIE_FILE=/run/tbankrot-auth/cookies.json" in workflow
    assert "Auth Center will request interactive login" in workflow
    assert "source=$env:CONFIG_ROOT\\tbankrot-auth,target=/run/tbankrot-auth" in workflow
    assert "source=$env:CONFIG_ROOT\\tbankrot-auth,target=/run/tbankrot-auth,readonly" in workflow
    assert "--shm-size 256m" in workflow
    assert "Protected TBankrot session is missing" not in workflow


def test_production_image_contains_playwright_chromium_runtime() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "PLAYWRIGHT_BROWSERS_PATH=/ms-playwright" in dockerfile
    assert "python -m playwright install --with-deps chromium" in dockerfile


def test_legacy_tbankrot_cookie_migration_cannot_block_deploy() -> None:
    workflow = HOME_DEPLOY.read_text(encoding="utf-8")

    assert "Legacy TBankrot cookie migration skipped" in workflow
    assert "Auth Center will request interactive TBankrot login; production deploy continues" in workflow
    assert "Copy-Item -LiteralPath $legacyTbankrotCookiePath" in workflow
    assert "-ErrorAction Stop" in workflow
