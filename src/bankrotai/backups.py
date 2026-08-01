from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from bankrotai.core import get_settings


_BACKUP_LOCK = threading.Lock()


@dataclass(frozen=True)
class BackupVerification:
    path: Path
    integrity: str
    alembic_version: str | None
    processed_lots: int
    size_bytes: int

    @property
    def is_valid(self) -> bool:
        return self.integrity == "ok"


def sqlite_database_path(database_url: str | None = None) -> Path:
    url = database_url or get_settings().database_url
    parsed = urlparse(url)
    if parsed.scheme != "sqlite":
        raise ValueError("Desktop backup supports SQLite databases only")
    raw_path = unquote(url.removeprefix("sqlite:///"))
    if url.startswith("sqlite:////"):
        raw_path = "/" + raw_path.lstrip("/")
    elif parsed.netloc and parsed.netloc not in {"", "localhost"}:
        raw_path = f"//{parsed.netloc}/{raw_path.lstrip('/')}"
    path = Path(raw_path or "bankrotai.db")
    return path if path.is_absolute() else Path.cwd() / path


def verify_sqlite_backup(path: str | Path) -> BackupVerification:
    backup_path = Path(path).resolve()
    if not backup_path.is_file():
        raise FileNotFoundError(backup_path)
    with closing(sqlite3.connect(f"file:{backup_path.as_posix()}?mode=ro", uri=True)) as connection:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        version = None
        lots = 0
        if "alembic_version" in tables:
            row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            version = str(row[0]) if row else None
        if "processed_lots" in tables:
            lots = int(connection.execute("SELECT COUNT(*) FROM processed_lots").fetchone()[0])
    return BackupVerification(
        path=backup_path,
        integrity=integrity,
        alembic_version=version,
        processed_lots=lots,
        size_bytes=backup_path.stat().st_size,
    )


def create_sqlite_backup(
    destination_dir: str | Path | None = None,
    *,
    database_url: str | None = None,
    label: str = "daily",
    retain: int = 14,
) -> BackupVerification:
    source_path = sqlite_database_path(database_url).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    configured_dir: str | Path = destination_dir or os.getenv("BACKUP_DIR") or "backups"
    backup_dir = Path(configured_dir).resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = backup_dir / f"bankrotai-{label}-{timestamp}.db"

    with _BACKUP_LOCK:
        descriptor, temporary_name = tempfile.mkstemp(prefix=".bankrotai-", suffix=".db", dir=backup_dir)
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with closing(sqlite3.connect(source_path)) as source, closing(sqlite3.connect(temporary)) as destination:
                source.backup(destination)
            verification = verify_sqlite_backup(temporary)
            if not verification.is_valid:
                raise RuntimeError(f"Backup integrity check failed: {verification.integrity}")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)

        if retain > 0:
            backups = sorted(
                backup_dir.glob(f"bankrotai-{label}-*.db"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            for expired in backups[retain:]:
                expired.unlink()
    return verify_sqlite_backup(target)


def ensure_daily_sqlite_backup(
    destination_dir: str | Path | None = None,
    *,
    database_url: str | None = None,
    retain: int = 14,
) -> BackupVerification | None:
    configured_dir: str | Path = destination_dir or os.getenv("BACKUP_DIR") or "backups"
    backup_dir = Path(configured_dir).resolve()
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    if any(backup_dir.glob(f"bankrotai-daily-{today}T*.db")):
        return None
    return create_sqlite_backup(
        backup_dir,
        database_url=database_url,
        label="daily",
        retain=retain,
    )


def restore_sqlite_backup(
    backup_path: str | Path,
    *,
    database_url: str | None = None,
    safety_backup_dir: str | Path | None = None,
) -> BackupVerification:
    source = verify_sqlite_backup(backup_path)
    if not source.is_valid:
        raise RuntimeError(f"Refusing invalid backup: {source.integrity}")
    destination = sqlite_database_path(database_url).resolve()
    if destination.exists():
        create_sqlite_backup(
            safety_backup_dir,
            database_url=database_url,
            label="before-restore",
            retain=5,
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _BACKUP_LOCK:
        with closing(sqlite3.connect(source.path)) as backup, closing(sqlite3.connect(destination)) as restored:
            backup.backup(restored)
    return verify_sqlite_backup(destination)
