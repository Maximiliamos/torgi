"""Durable exact-identity exclusions for explicitly user-deleted source cards."""
import hashlib
import json

from sqlalchemy import select

from bankrotai.db import AppSetting, utc_now

PREFIX = "source-lot-exclusion:"


class SourceLotExcludedError(ValueError):
    pass


def exclusion_key(source_system: str, external_id: str) -> str:
    if not source_system or not external_id or len(source_system) > 50 or len(external_id) > 100:
        raise ValueError("Invalid source exclusion identity")
    return PREFIX + hashlib.sha256((source_system + "\0" + external_id).encode()).hexdigest()


def excluded_ids(session, source_system: str, external_ids) -> set[str]:
    requested = {exclusion_key(source_system, str(identity)): str(identity) for identity in external_ids}
    result = set()
    keys = list(requested)
    for offset in range(0, len(keys), 500):
        for row in session.scalars(select(AppSetting).where(AppSetting.key.in_(keys[offset:offset + 500]))):
            value = json.loads(row.value or "null")
            if not isinstance(value, dict) or (value.get("source_system"), value.get("external_id")) != (source_system, requested[row.key]):
                raise ValueError("Corrupted exact source exclusion; refuse ingestion")
            result.add(requested[row.key])
    return result


def register_exclusion(session, *, source_system: str, external_id: str, reason: str) -> bool:
    key = exclusion_key(source_system, external_id)
    if excluded_ids(session, source_system, [external_id]):
        return False
    if not reason.strip():
        raise ValueError("An explicit user-decision reason is required")
    session.add(AppSetting(key=key, value=json.dumps({
        "source_system": source_system, "external_id": external_id,
        "reason": reason, "created_at_utc": utc_now().isoformat(),
    })))
    return True


def assert_not_excluded(session, lots) -> None:
    grouped: dict[str, list[str]] = {}
    for lot in lots:
        grouped.setdefault(lot.source_system, []).append(lot.external_id)
    for source_system, external_ids in grouped.items():
        if excluded_ids(session, source_system, external_ids):
            raise SourceLotExcludedError("Лот исключён из повторного импорта по решению пользователя.")
