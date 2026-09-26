from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "scripts" / "backup-home-postgres.ps1"
HEALTH = ROOT / "scripts" / "phase3-production-health.ps1"
BACKUP_WORKFLOW = ROOT / ".github" / "workflows" / "phase3-lite-backup.yml"
HEALTH_WORKFLOW = ROOT / ".github" / "workflows" / "phase3-lite-health.yml"


def test_postgres_restore_drill_checks_critical_application_data() -> None:
    script = BACKUP.read_text(encoding="utf-8")
    for table in (
        "processed_lots",
        "lot_geo_snapshots",
        "app_users",
        "lot_sync_runs",
        "map_datasets",
    ):
        assert table in script
    assert "source_schema_revision" in script
    assert "restored_schema_revision" in script
    assert "restore_verification = $restoreStatus" in script
    assert "sha256 = $checksum" in script
    assert "postgres:17" in script
    assert "--network none" in script


def test_phase3_health_covers_runtime_data_and_disaster_recovery() -> None:
    script = HEALTH.read_text(encoding="utf-8")
    for container in (
        "bankrotai-home-postgres",
        "bankrotai-home-redis",
        "bankrotai-photon",
        "bankrotai-home-secondary",
        "bankrotai-home-ingestion-worker",
        "bankrotai-home-geocoding-worker",
        "bankrotai-home-map-worker",
    ):
        assert container in script
    assert "build_phase3_health" in script
    assert "backup-recent" in script
    assert "restore-verification-recent" in script
    assert "MaxBackupAgeHours = 30" in script
    assert "MaxVerifiedRestoreAgeHours = 192" in script


def test_phase3_workflows_schedule_health_and_restore_drills_with_deduplicated_alerts() -> None:
    health = HEALTH_WORKFLOW.read_text(encoding="utf-8")
    backup = BACKUP_WORKFLOW.read_text(encoding="utf-8")

    assert "17,47 * * * *" in health
    assert "workflow_run:" in health
    assert "Deploy home secondary origin" in health
    assert "Phase 3 Lite full source reconciliation" in health
    assert "[Phase 3] Production health alert" in health
    assert "[Phase 3] Source health warning" in health
    assert "warning_count" in health
    assert "needs.inspect.outputs.warning_count != '0'" in health
    assert "listForRepo" in health
    assert "state: 'closed'" in health

    assert "17 2 * * 1-6" in backup
    assert "47 2 * * 0" in backup
    assert "-VerifyRestore:$verify" in backup
    assert "[Phase 3] Backup/restore alert" in backup
    assert "cancel-in-progress: false" in backup
