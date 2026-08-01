from __future__ import annotations

from sqlalchemy import func, inspect, select, text

from bankrotai.db import Base, get_engine


def main() -> int:
    engine = get_engine()
    if engine.dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL verification requires DATABASE_URL")
    failures: list[str] = []
    with engine.connect() as connection:
        counts = {
            table.name: int(connection.scalar(select(func.count()).select_from(table)) or 0)
            for table in Base.metadata.sorted_tables
        }
        for table in inspect(engine).get_table_names():
            for foreign_key in inspect(engine).get_foreign_keys(table):
                columns = foreign_key.get("constrained_columns") or []
                parent_columns = foreign_key.get("referred_columns") or []
                parent = foreign_key.get("referred_table")
                if len(columns) != 1 or len(parent_columns) != 1 or not parent:
                    continue
                orphan_count = int(connection.scalar(text(
                    f'SELECT COUNT(*) FROM "{table}" c LEFT JOIN "{parent}" p '
                    f'ON c."{columns[0]}" = p."{parent_columns[0]}" '
                    f'WHERE c."{columns[0]}" IS NOT NULL AND p."{parent_columns[0]}" IS NULL'
                )) or 0)
                if orphan_count:
                    failures.append(f"{table}.{columns[0]} has {orphan_count} orphan rows")
    print({"counts": counts, "foreign_key_errors": failures})
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
