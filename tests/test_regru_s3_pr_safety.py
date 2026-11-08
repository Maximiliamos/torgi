from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "regru-s3-diagnostics.yml"


def test_regru_s3_pull_request_forces_credentialless_mode_before_secret_detection() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    validate = text.split("- name: Validate S3 configuration", 1)[1].split(
        "- name: Ensure AWS CLI is available", 1
    )[0]
    pr_guard = 'if test "$GITHUB_EVENT_NAME" = "pull_request"; then'
    secret_check = 'elif test -n "$MAP_OBJECT_STORE_ACCESS_KEY"'
    assert pr_guard in validate
    assert secret_check in validate
    assert validate.index(pr_guard) < validate.index(secret_check)
    assert "HAS_S3_CREDENTIALS=false" in validate
    assert "no production S3 mutations are allowed from PR validation" in validate
