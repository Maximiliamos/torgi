"""Protect production P11 from concurrent full-source ingestion load."""

from pathlib import Path


WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github/workflows/phase3-lite-full-reconcile.yml"
).read_text(encoding="utf-8")


def test_push_reconciliation_starts_only_after_current_sha_p11_terminates() -> None:
    wait = WORKFLOW.index("Avoid full ingestion competing with exact-SHA P11 soak")
    reconcile = WORKFLOW.index("  reconcile:\n")
    assert wait < reconcile
    block = WORKFLOW[wait:reconcile]
    assert "if: github.event_name == 'push'" in block
    assert 'r.get("name")=="P11 production acceptance"' in block
    assert 'r.get("head_sha")==__import__("os").environ["GITHUB_SHA"]' in block
    assert 'if [ "$state" = "completed" ]; then' in block
    assert "refusing overlapping full ingestion" in block
    assert "exit 1" in block
    assert "needs: wait-home-deploy" in WORKFLOW[reconcile:]


def test_wait_does_not_turn_failed_p11_into_accepted_release() -> None:
    # A failed P11 may release the source-ingestion queue, but the exact-SHA
    # release workflow must still require P11 conclusion=SUCCESS separately.
    release = (
        Path(__file__).resolve().parents[1]
        / ".github/workflows/release.yml"
    ).read_text(encoding="utf-8")
    assert '"P11 production acceptance"' in release
    assert 'if all(value == "success" for value in states.values()):' in release
