from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from bankrotai.db import AppSetting, utc_now


_STATUS_KEYS = {
    "maintenance": "operations_status:maintenance",
    "backup": "operations_status:backup",
    "runner": "operations_status:runner",
}


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _parse_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return _naive_utc(value)
    try:
        return _naive_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def record_operations_snapshot(session: Any, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    key = _STATUS_KEYS.get(str(kind))
    if key is None:
        raise ValueError(f"Unsupported operations snapshot kind: {kind}")
    value = dict(payload)
    value.setdefault("recorded_at", utc_now().isoformat())
    serialized = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    row = session.scalar(select(AppSetting).where(AppSetting.key == key))
    if row is None:
        row = AppSetting(key=key, value=serialized)
        session.add(row)
    else:
        row.value = serialized
    session.flush()
    return value


def _read_snapshot(session: Any, kind: str) -> dict[str, Any] | None:
    key = _STATUS_KEYS[kind]
    row = session.scalar(select(AppSetting).where(AppSetting.key == key))
    if row is None or not row.value:
        return None
    try:
        payload = json.loads(str(row.value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {
            "healthy": False,
            "error": "invalid_persisted_operations_snapshot",
            "recorded_at": row.updated_at,
        }
    if not isinstance(payload, dict):
        return {
            "healthy": False,
            "error": "invalid_persisted_operations_snapshot",
            "recorded_at": row.updated_at,
        }
    payload.setdefault("recorded_at", row.updated_at)
    return payload


def operations_host_status(session: Any, *, now: datetime | None = None) -> dict[str, Any]:
    current = _naive_utc(now or utc_now()) or utc_now()
    maintenance = _read_snapshot(session, "maintenance")
    backup = _read_snapshot(session, "backup")
    runner = _read_snapshot(session, "runner")

    disk_free_gb = None
    maintenance_checked_at = None
    if maintenance:
        disk_free_gb = maintenance.get("disk_free_gb_after")
        maintenance_checked_at = _parse_timestamp(
            maintenance.get("checked_at") or maintenance.get("recorded_at")
        )

    backup_created_at = _parse_timestamp(
        (backup or {}).get("created_at") or (backup or {}).get("recorded_at")
    )
    backup_age_hours = (
        round(max(0.0, (current - backup_created_at).total_seconds()) / 3600, 2)
        if backup_created_at
        else None
    )
    runner_checked_at = _parse_timestamp(
        (runner or {}).get("checked_at") or (runner or {}).get("recorded_at")
    )
    runner_age_seconds = (
        round(max(0.0, (current - runner_checked_at).total_seconds()), 1)
        if runner_checked_at
        else None
    )

    return {
        "maintenance": maintenance,
        "backup": backup,
        "runner": runner,
        "disk": {
            "free_gb": disk_free_gb,
            "critical_below_gb": 15,
            "recommended_gb": 25,
            "checked_at": maintenance_checked_at,
            "healthy": bool(
                isinstance(disk_free_gb, (int, float)) and float(disk_free_gb) >= 15
            ),
        },
        "backup_age_hours": backup_age_hours,
        "backup_healthy": bool(
            backup
            and str(backup.get("restore_verification") or "") == "passed"
            and backup_age_hours is not None
            and backup_age_hours <= 30
        ),
        "runner_age_seconds": runner_age_seconds,
        "runner_healthy": bool(
            runner
            and runner.get("healthy") is True
            and runner_age_seconds is not None
            and runner_age_seconds <= 3600
        ),
    }
