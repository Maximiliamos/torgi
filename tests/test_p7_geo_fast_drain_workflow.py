from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "p7-geo-fast-drain.yml"
TASKS = ROOT / "src" / "bankrotai" / "tasks.py"
SERVICE = ROOT / "src" / "bankrotai" / "services" / "geo_fast_drain.py"


def test_p7_workflow_waits_for_exact_home_deploy_and_monitors_disk() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "Wait for exact Home production revision" in workflow
    assert "head_sha=$GITHUB_SHA" in workflow
    assert "Deploy home secondary origin" in workflow
    assert "P7 disk preflight" in workflow
    assert "$freeGb -lt 10" in workflow
    assert "$freeGb -lt 8" in workflow
    assert "set_geocoding_paused" in workflow
    assert "Monitor controlled GEO drain" in workflow
    assert "p7-geo-fast-drain.json" in workflow


def test_p7_task_is_isolated_on_geocoding_queue_and_long_bounded() -> None:
    tasks = TASKS.read_text(encoding="utf-8")

    assert '"bankrotai.tasks.geo_fast_drain_task": {"queue": _QUEUE_GEOCODING}' in tasks
    assert 'name="bankrotai.tasks.geo_fast_drain_task"' in tasks
    assert "soft_time_limit=4 * 60 * 60" in tasks
    assert "time_limit=5 * 60 * 60" in tasks
    assert "schedule_geo_fast_drain" in tasks
    assert 'BackgroundTaskState.task_type == "geocoding_fast_drain"' in tasks


def test_p7_service_uses_held_waves_not_bulk_immediate_release() -> None:
    service = SERVICE.read_text(encoding="utf-8")

    assert 'P7_HOLD_STATUS = "p7_queued"' in service
    assert "P7_DEFAULT_WAVE_SIZE = 2_000" in service
    assert "P7_DEFAULT_BATCH_LIMIT = 500" in service
    assert "P7_MAX_BATCHES = 64" in service
    assert "release_geo_fast_drain_wave" in service
    assert "ProcessedLot.region_code.in_(CFO_REGION_CODES)" in service
    assert "legacy_attempt_count" in service
    assert "p7_reclassified" in service
