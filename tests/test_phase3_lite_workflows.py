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
    assert "BackupDirectory = 'D:\\BankrotAI\\dr-backups'" in script
    assert "MaxBackupAgeHours = 60" in script
    assert "MaxVerifiedRestoreAgeHours = 60" in script
    assert "disk-c-headroom" in script
    assert "recommended_gb = 25" in script


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

    assert "17 2 * * *" in backup
    assert "Latest verified D: backup age" in backup
    assert "$ageHours -lt 46" in backup
    assert "-Destination 'D:\\BankrotAI\\dr-backups'" in backup
    assert "-VerifyRestore" in backup
    assert "-RetainCount 1" in backup
    assert "Remove superseded C backup roots after verified D backup" in backup
    assert "docker volume prune" not in backup
    assert "[Phase 3] Backup/restore alert" in backup
    assert "cancel-in-progress: false" in backup
    assert "wait-map-retention:" in backup
    assert "P2 production maintenance" in backup
    assert "Wait for map retention on the same main revision" in backup
    assert "needs: wait-map-retention" in backup

    full_reconcile = (ROOT / ".github" / "workflows" / "phase3-lite-full-reconcile.yml").read_text(encoding="utf-8")
    home_deploy = (ROOT / ".github" / "workflows" / "home-secondary-deploy.yml").read_text(encoding="utf-8")
    assert "_unpaused_source_specs(default_source_specs())" in full_reconcile
    assert "'.github/workflows/home-secondary-deploy.yml'" in full_reconcile
    assert "'src/bankrotai/services/map_builder.py'" in full_reconcile
    assert "'src/bankrotai/torgi_russia.py'" in full_reconcile
    assert "'src/bankrotai/connectors/registry/torgi_russia.py'" in full_reconcile
    assert "configured_sources" in full_reconcile
    assert "source set mismatch" in full_reconcile
    assert "progress_at" in full_reconcile
    assert "lease_expires_at" in full_reconcile
    assert "CELERY_NATIONWIDE_SOFT_TIME_LIMIT=14400" in home_deploy
    assert "CELERY_NATIONWIDE_HARD_TIME_LIMIT=18000" in home_deploy
    assert "Nationwide Celery task limits" in home_deploy
    assert "stalled: no durable source progress" in full_reconcile
    assert "did not reach a terminal state within 85 minutes" not in full_reconcile
    assert "did not reach success within retry deadline" not in full_reconcile


def test_backup_policy_uses_d_drive_single_verified_copy_and_safe_migration() -> None:
    backup_script = BACKUP.read_text(encoding="utf-8")
    workflow = BACKUP_WORKFLOW.read_text(encoding="utf-8")
    migration = (ROOT / "scripts" / "migrate-backups-to-d.ps1").read_text(encoding="utf-8")

    assert "Destination = 'D:\\BankrotAI\\dr-backups'" in backup_script
    assert "RetainCount = 1" in backup_script
    assert "map_storage = $mapStorage" in backup_script
    assert "Retention skipped because the new backup has not passed isolated restore verification." in backup_script
    assert "restoreStatus -ne 'passed'" in backup_script
    assert "Remove superseded C backup roots after verified D backup" in workflow
    assert "D: backup SHA-256 mismatch" in migration
    assert "docker volume prune" not in workflow
    assert "docker volume prune" not in migration
