from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from bankrotai.backups import (
    create_sqlite_backup,
    ensure_daily_sqlite_backup,
    restore_sqlite_backup,
    verify_sqlite_backup,
)


def _database(path: Path, *, title: str = "original") -> str:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES ('test-head')")
        connection.execute("CREATE TABLE processed_lots (id INTEGER PRIMARY KEY, title TEXT NOT NULL)")
        connection.execute("INSERT INTO processed_lots (title) VALUES (?)", (title,))
        connection.commit()
    return f"sqlite:///{path.as_posix()}"


def test_backup_is_consistent_and_daily_backup_is_idempotent(tmp_path: Path) -> None:
    database_url = _database(tmp_path / "source.db")
    backup_dir = tmp_path / "backups"

    result = create_sqlite_backup(backup_dir, database_url=database_url, label="manual")
    daily = ensure_daily_sqlite_backup(backup_dir, database_url=database_url)
    duplicate_daily = ensure_daily_sqlite_backup(backup_dir, database_url=database_url)

    assert result.is_valid
    assert result.processed_lots == 1
    assert result.alembic_version == "test-head"
    assert daily is not None and daily.is_valid
    assert duplicate_daily is None
    assert verify_sqlite_backup(result.path) == result


def test_restore_replaces_database_from_verified_backup(tmp_path: Path) -> None:
    source_url = _database(tmp_path / "source.db", title="from backup")
    backup = create_sqlite_backup(tmp_path / "backups", database_url=source_url, label="manual")
    restored_path = tmp_path / "restored.db"
    restored_url = f"sqlite:///{restored_path.as_posix()}"

    result = restore_sqlite_backup(backup.path, database_url=restored_url)

    assert result.is_valid
    with closing(sqlite3.connect(restored_path)) as connection:
        assert connection.execute("SELECT title FROM processed_lots").fetchone()[0] == "from backup"
