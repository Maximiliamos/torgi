from pathlib import Path


WORKFLOW = Path(".github/workflows/home-performance-diagnostics.yml")


def test_home_performance_diagnostics_is_read_only_and_records_disk_placement() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert "Get-PhysicalDisk" in workflow
    assert "Get-Volume" in workflow
    assert "Docker\\wsl\\data\\ext4.vhdx" in workflow
    assert "bankrotai-home-postgres" in workflow
    assert "bankrotai-photon" in workflow
    assert "docker stats --no-stream" in workflow
    assert "docker rm" not in workflow
    assert "docker run" not in workflow
    assert "docker volume rm" not in workflow
    assert "secrets." not in workflow
