from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def test_release_requires_current_main_acceptance() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "Require accepted SHA to still be current main" in text
    assert "/branches/main" in text
    assert 'accepted == current' in text
    assert "RELEASE_CURRENT=" in text
    assert text.count("if: env.RELEASE_CURRENT == 'true'") >= 7


def test_release_keeps_exact_sha_production_gates() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    for marker in (
        '"Production reliability"',
        '"P1 production data quality"',
        '"Deploy WEB and API to REG.RU"',
        '"Deploy Cloudflare edge proxy"',
        '"Public WEB smoke"',
        '"Production functional reliability"',
        '"P11 production acceptance"',
    ):
        assert marker in text
    assert "head_sha=" in text
