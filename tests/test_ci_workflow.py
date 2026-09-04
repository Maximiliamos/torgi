from pathlib import Path


CI_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def test_npm_security_audits_are_bounded_retried_and_fail_closed() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "audit_with_retry --audit-level=high" in workflow
    assert "audit_with_retry --omit=dev --audit-level=high" in workflow
    assert "for attempt in 1 2 3" in workflow
    assert "timeout 180 npm audit" in workflow
    assert 'if test "$attempt" -eq 3' in workflow
    assert "return 1" in workflow
