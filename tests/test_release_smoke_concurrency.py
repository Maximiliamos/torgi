"""Post-deploy smoke cannot be canceled by an unrelated event/commit.

GitHub release attestation is still fail-closed: only a SUCCESSFUL exact SHA
run counts as acceptance. Cancelled/skipped runs remain untrusted.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / ".github/workflows"


def test_exact_sha_production_smokes_cannot_cancel_each_other():
    for name,group in (
        ("public-web-smoke.yml", "public-web-smoke"),
        ("production-functional.yml", "production-functional-reliability"),
    ):
        content = (ROOT / name).read_text(encoding="utf-8")
        assert "github.event.workflow_run.head_sha" in content
        assert f"group: {group}-" in content
        assert "cancel-in-progress: ${{ github.event_name != 'workflow_run' }}" in content
        assert "github.event.workflow_run.conclusion == 'success'" in content


def test_release_never_accepts_cancelled_or_skipped_as_success():
    content = (ROOT / "release.yml").read_text(encoding="utf-8")
    assert 'run.get("conclusion") == "success"' in content
    assert '"failure", "timed_out", "action_required", "startup_failure"' in content
    assert 'states[name] = "retryable"' in content
    assert 'if all(value == "success" for value in states.values()):' in content
    assert 'if failed:' in content
    assert 'Timed out waiting for exact-SHA gates:' in content
    assert '"cancelled", "skipped"' not in content.split(
        'states[name] = "failed"', 1
    )[0]
