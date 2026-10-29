"""Regression checks for the bounded Home Docker build release gate."""
from pathlib import Path
import re


def test_home_build_allows_slow_apt_and_chromium_without_disabling_fallback_guard():
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github/workflows/home-secondary-deploy.yml"
    ).read_text(encoding="utf-8")
    attempts = re.findall(
        r"\[pscustomobject\]@\{ Pull = \$(true|false);\s+Timeout = (\d+);\s+Mode = '([^']+)' \}",
        workflow,
    )
    assert attempts == [
        ("true", "900", "refresh-base"),
        ("false", "900", "cached-base"),
    ]
    # Still bounded: the job has an overall timeout and kills timed-out docker
    # processes. It must not permit a stale image with mismatched dependencies.
    assert "timeout-minutes: 120" in workflow
    assert "WaitForExit($TimeoutSeconds * 1000)" in workflow
    assert "taskkill.exe /PID $process.Id /T /F" in workflow
    assert "git diff --quiet $baseSha $env:GITHUB_SHA -- requirements.lock certs" in workflow
    assert "$baseNormalized -cne $currentNormalized" in workflow
    assert "Offline source-overlay fallback refused: Python dependencies" in workflow
