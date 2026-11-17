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
    assert "audit_with_retry --omit=dev --audit-level=high" in workflow
    assert "npm audit --json --audit-level=high" in workflow
    assert "node --test scripts/check-build-audit.test.mjs" in workflow
    assert "| node scripts/check-build-audit.mjs" in workflow

def test_feature_branches_use_single_pull_request_ci_run() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    assert 'push:\n    branches: [main]' in workflow
    assert 'pull_request:\n    branches: [main]' in workflow
    assert 'branches: ["**"]' not in workflow

