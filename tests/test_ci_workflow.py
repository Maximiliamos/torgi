from pathlib import Path


CI_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def test_npm_security_audits_are_bounded_retried_and_fail_closed() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "audit_with_retry --omit=dev --audit-level=high" in workflow
    assert "for attempt in 1 2 3" in workflow
    assert "timeout 180 npm audit" in workflow
    assert 'if test "$attempt" -eq 3' in workflow
    assert "return 1" in workflow


def test_unpatched_dev_advisory_exception_is_exact_and_runtime_stays_strict() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "GHSA-vfj7-8cjw-p6xm" in workflow
    assert "grep -vx 'GHSA-vfj7-8cjw-p6xm'" in workflow
    assert "Unexpected high/critical npm advisory set" in workflow
    assert "runtime npm audit is clean" in workflow
