from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_map_compaction_requires_fresh_d_drive_backup() -> None:
    script = (ROOT / "scripts" / "compact-map-storage.ps1").read_text(encoding="utf-8")
    assert "BackupDirectory = 'D:\\BankrotAI\\dr-backups'" in script
    assert "MaxBackupAgeHours = 60" in script
    assert "VACUUM (FULL, ANALYZE) map_tiles" in script
    assert "bounded_dataset_count" in script
    assert "recent_backup" in script
    assert "enough_free_space" in script
