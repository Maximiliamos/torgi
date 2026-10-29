"""The offline Home overlay may reuse dependencies only for a version-only bump."""
from pathlib import Path


WORKFLOW = Path(".github/workflows/home-secondary-deploy.yml")


def test_offline_overlay_permits_project_version_only_and_rejects_other_changes():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "git diff --quiet $baseSha $env:GITHUB_SHA -- requirements.lock certs" in workflow
    assert "git diff --quiet $baseSha $env:GITHUB_SHA -- requirements.lock pyproject.toml certs" not in workflow
    assert "function Normalize-OverlayPyproject" in workflow
    assert "if ($inProject -and $line -match" in workflow
    assert "version\\s*=\\s*" in workflow
    assert "$normalized += 'version = \"<release-only>\"'" in workflow
    assert 'git.exe show "${baseSha}:pyproject.toml"' in workflow
    assert "Get-Content -LiteralPath 'pyproject.toml'" in workflow
    assert "$baseNormalized -cne $currentNormalized" in workflow
    assert "pyproject differs beyond [project].version" in workflow
    assert "project version is not" not in workflow  # old fuzzy check removed
    assert "Offline source-overlay version-only metadata proof: PASS" in workflow
    assert "Offline source-overlay fallback refused: Python dependencies" in workflow
    assert "deployed Python $actualPython != required $expectedPython" in workflow
    assert "offline source-overlay Docker build" in workflow
    assert "taskkill.exe /PID $process.Id /T /F" in workflow


def test_offline_overlay_still_uses_source_only_image_layer():
    dockerfile = Path("Dockerfile.home-overlay").read_text(encoding="utf-8")
    assert "FROM ${BASE_IMAGE}" in dockerfile
    assert "COPY src ./src" in dockerfile
    assert "pip install --no-deps ." in dockerfile
    assert "pip install -r requirements.lock" not in dockerfile
    assert "playwright install" not in dockerfile
