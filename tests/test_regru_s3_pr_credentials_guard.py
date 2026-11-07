"""Prevent credentialed REG.RU S3 diagnostics on pull requests.

This checks both the event guard and expression-level secret withholding:
a pull_request from the same repository can otherwise see repo secrets.
"""
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/regru-s3-diagnostics.yml"


def test_s3_pr_credentialless_guard_precedes_secret_detection():
    content = WORKFLOW.read_text(encoding="utf-8")
    validate = content.split("- name: Validate S3 configuration", 1)[1].split(
        "- name: Ensure AWS CLI is available", 1
    )[0]
    assert 'if test "$GITHUB_EVENT_NAME" = "pull_request"; then' in validate
    assert "HAS_S3_CREDENTIALS=false" in validate
    assert "no production S3 mutations are allowed from PR validation" in validate
    assert validate.index('if test "$GITHUB_EVENT_NAME" = "pull_request"; then') < validate.index(
        'elif test -n "$MAP_OBJECT_STORE_ACCESS_KEY"'
    )


def test_s3_secrets_are_not_mapped_into_pr_job_env():
    content = WORKFLOW.read_text(encoding="utf-8")
    for key in (
        "MAP_OBJECT_STORE_ACCESS_KEY",
        "MAP_OBJECT_STORE_SECRET_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
    ):
        line = next(line for line in content.splitlines() if line.strip().startswith(key + ":"))
        assert "github.event_name == 'workflow_dispatch'" in line
        assert "github.ref == 'refs/heads/main'" in line
        assert "|| ''" in line


def test_s3_mutations_require_authenticated_flag():
    content = WORKFLOW.read_text(encoding="utf-8")
    for name in ("Configure browser CORS", "Upload probe with production signer", "Delete probe"):
        section = content.split("- name: " + name, 1)[1].split("- name:", 1)[0]
        assert "HAS_S3_CREDENTIALS == 'true'" in section
