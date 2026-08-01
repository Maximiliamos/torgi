from __future__ import annotations

import argparse
import os
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import MetaData, create_engine, func, inspect, select, text

from bankrotai.db import Base


def batches(rows: Iterable[dict], size: int = 500) -> Iterable[list[dict]]:
    batch: list[dict] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def require_secure_direct_postgres(url: str) -> None:
    parsed = urlparse(url)
    if not parsed.scheme.startswith("postgresql"):
        raise ValueError("Target must be PostgreSQL")
    if "sslmode=require" not in parsed.query or "channel_binding=require" not in parsed.query:
        raise ValueError("Target URL must require TLS and channel binding")
    if "-pooler." in (parsed.hostname or ""):
        raise ValueError("Use the direct Neon URL for migrations, not the pooled URL")


def migrate(sqlite_path: Path, target_url: str) -> dict[str, int]:
    require_secure_direct_postgres(target_url)
    source = create_engine(f"sqlite:///{sqlite_path.resolve().as_posix()}")
    target = create_engine(target_url, pool_pre_ping=True)
    source_meta = MetaData()
    source_meta.reflect(source)
    counts: dict[str, int] = {}
    with source.connect() as source_connection, target.begin() as target_connection:
        for target_table in Base.metadata.sorted_tables:
            if target_table.name not in source_meta.tables:
                continue
            existing = target_connection.scalar(select(func.count()).select_from(target_table)) or 0
            source_table = source_meta.tables[target_table.name]
            source_count = source_connection.scalar(select(func.count()).select_from(source_table)) or 0
            if existing:
                raise RuntimeError(f"Target table {target_table.name} is not empty ({existing} rows)")
            common = [column.name for column in target_table.columns if column.name in source_table.c]
            rows = source_connection.execute(select(*(source_table.c[name] for name in common))).mappings()
            delayed_duplicates: list[tuple[int, int]] = []
            for batch in batches((dict(row) for row in rows)):
                if target_table.name == "processed_lots":
                    for row in batch:
                        duplicate = row.get("duplicate_of_id")
                        if duplicate is not None:
                            delayed_duplicates.append((int(row["id"]), int(duplicate)))
                            row["duplicate_of_id"] = None
                target_connection.execute(target_table.insert(), batch)
            for lot_id, duplicate_id in delayed_duplicates:
                target_connection.execute(
                    target_table.update().where(target_table.c.id == lot_id).values(duplicate_of_id=duplicate_id)
                )
            counts[target_table.name] = int(source_count)

        for table in Base.metadata.sorted_tables:
            if "id" not in table.c or table.name not in counts:
                continue
            target_connection.execute(text(
                "SELECT setval(pg_get_serial_sequence(:table_name, 'id'), "
                "GREATEST(COALESCE((SELECT MAX(id) FROM \"" + table.name + "\"), 1), 1), true)"
            ), {"table_name": table.name})
    return counts


def verify(sqlite_path: Path, target_url: str) -> tuple[dict[str, tuple[int, int]], list[str]]:
    require_secure_direct_postgres(target_url)
    source = create_engine(f"sqlite:///{sqlite_path.resolve().as_posix()}")
    target = create_engine(target_url, pool_pre_ping=True)
    source_meta = MetaData()
    source_meta.reflect(source)
    mismatches: dict[str, tuple[int, int]] = {}
    orphan_errors: list[str] = []
    with source.connect() as src, target.connect() as dst:
        for table in Base.metadata.sorted_tables:
            if table.name not in source_meta.tables:
                continue
            source_count = int(src.scalar(select(func.count()).select_from(source_meta.tables[table.name])) or 0)
            target_count = int(dst.scalar(select(func.count()).select_from(table)) or 0)
            if source_count != target_count:
                mismatches[table.name] = (source_count, target_count)
        inspector = inspect(target)
        for table in inspector.get_table_names():
            for foreign_key in inspector.get_foreign_keys(table):
                constrained = foreign_key.get("constrained_columns") or []
                referred = foreign_key.get("referred_columns") or []
                referred_table = foreign_key.get("referred_table")
                if len(constrained) != 1 or len(referred) != 1 or not referred_table:
                    continue
                query = text(
                    f'SELECT COUNT(*) FROM "{table}" child LEFT JOIN "{referred_table}" parent '
                    f'ON child."{constrained[0]}" = parent."{referred[0]}" '
                    f'WHERE child."{constrained[0]}" IS NOT NULL AND parent."{referred[0]}" IS NULL'
                )
                orphan_count = int(dst.scalar(query) or 0)
                if orphan_count:
                    orphan_errors.append(f"{table}.{constrained[0]}: {orphan_count} orphan rows")
    return mismatches, orphan_errors


def main() -> int:
    parser = argparse.ArgumentParser(description="One-time verified SQLite to Neon PostgreSQL copy")
    parser.add_argument("sqlite", type=Path)
    parser.add_argument("--target-env", default="DATABASE_MIGRATION_URL")
    parser.add_argument("--copy", action="store_true", help="Copy into an empty migrated target database")
    args = parser.parse_args()
    target_url = os.getenv(args.target_env, "")
    if not target_url:
        parser.error(f"{args.target_env} is not configured")
    if not args.sqlite.is_file():
        parser.error(f"SQLite file does not exist: {args.sqlite}")
    if args.copy:
        copied = migrate(args.sqlite, target_url)
        print(f"Copied {sum(copied.values())} rows across {len(copied)} tables")
    mismatches, orphans = verify(args.sqlite, target_url)
    if mismatches or orphans:
        print(f"Count mismatches: {mismatches}")
        print(f"Foreign-key errors: {orphans}")
        return 2
    print("Verification passed: table counts match and no foreign-key orphans were found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
