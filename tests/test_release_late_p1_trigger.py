"""Exact-SHA release must recover automatically from late Home P1 completion."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")


def test_release_can_resume_on_lagging_p1_completion() -> None:
    assert 'workflows: ["P11 production acceptance", "P1 production data quality"]' in RELEASE
    assert "types: [completed]" in RELEASE
    assert "github.event.workflow_run.conclusion == 'success'" in RELEASE
    assert "github.event.workflow_run.event == 'push'" in RELEASE
    assert "github.event.workflow_run.head_branch == 'main'" in RELEASE


def test_retry_does_not_bypass_current_sha_production_acceptance() -> None:
    assert "Require accepted SHA to still be current main" in RELEASE
    assert "Wait for exact-SHA production gates" in RELEASE
    for workflow in (
        "P1 production data quality",
        "P11 production acceptance",
        "Production reliability",
        "Deploy WEB and API to REG.RU",
        "Deploy Cloudflare edge proxy",
        "Public WEB smoke",
        "Production functional reliability",
    ):
        assert workflow in RELEASE
    assert 'if all(value == "success" for value in states.values()):' in RELEASE
    assert 'if failed:' in RELEASE
    assert 'TAG="v$VERSION"' in RELEASE
    assert 'if [ "$EXISTING_TARGET" != "$ACCEPTED_SHA" ]; then' in RELEASE
