from __future__ import annotations

import argparse
import json
import re
import time
from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy import select

from bankrotai.db import AppSetting, GeoFailure, ProcessedLot, SessionLocal
from bankrotai.geo import CADASTRAL_GEOCODER
from bankrotai.services.stale_cadastral_recovery import recover_stale_cadastral_live_results


CAMPAIGN_VERSION = "2026-11-11-post1228-v1"
_SETTING_PREFIX = "geo_managed_stale_recovery:"
_MAX_BATCH_SIZE = 100
_DEFAULT_SOURCES = (
    "torgi-russia.ru",
    "torgi.gov.ru",
    "m-ets.ru",
    "lot-online.ru",
    "cdtrf.ru",
    "nistp.ru",
    "tbankrot.ru",
    "bidexpert.ru",
    "rus-on.ru",
    "sistematorg.com",
    "arbbitlot.ru",
    "centerr.ru",
    "etpu.ru",
    "aukcioncenter.ru",
)
_CADASTRAL = re.compile(r"^\d{2}:\d{1,2}:\d{5,9}:\d{1,12}$")


def _state_key(version: str) -> str:
    value = str(version or "").strip()
    if not value:
        raise ValueError("campaign version is required")
    key = f"{_SETTING_PREFIX}{value}"
    if len(key) > 100:
        raise ValueError("campaign version is too long")
    return key


def _initial_state(version: str, sources: Sequence[str]) -> dict[str, Any]:
    return {
        "version": version,
        "sources": list(sources),
        "source_index": 0,
        "cursors": {},
        "completed_sources": [],
        "probed": 0,
        "accepted": 0,
        "updated": 0,
        "operational_failures": 0,
    }


def _load_state(
    session: Any,
    *,
    version: str,
    sources: Sequence[str],
) -> tuple[dict[str, Any], AppSetting | None]:
    key = _state_key(version)
    setting = session.scalar(select(AppSetting).where(AppSetting.key == key))
    if setting is None or not setting.value:
        return _initial_state(version, sources), setting
    try:
        state = json.loads(setting.value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid managed recovery state for {version}") from exc
    if state.get("version") != version:
        raise ValueError("managed recovery state version mismatch")
    if list(state.get("sources") or []) != list(sources):
        raise ValueError("managed recovery source order changed within the same campaign")
    return state, setting


def _save_state(
    session: Any,
    *,
    version: str,
    state: dict[str, Any],
    setting: AppSetting | None,
) -> None:
    value = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    if setting is None:
        session.add(AppSetting(key=_state_key(version), value=value))
    else:
        setting.value = value


def _eligible_ids(
    session: Any,
    *,
    source: str,
    after_id: int,
    limit: int,
) -> list[int]:
    rows = session.scalars(
        select(ProcessedLot.id)
        .join(GeoFailure, GeoFailure.lot_id == ProcessedLot.id)
        .where(
            ProcessedLot.source_system == source,
            ProcessedLot.id > int(after_id),
            ProcessedLot.duplicate_of_id.is_(None),
            ProcessedLot.is_archived.is_(False),
            ProcessedLot.review_status.is_(None),
            ProcessedLot.auction_status.in_(("active", "scheduled")),
            ProcessedLot.current_geo_lat.is_(None),
            ProcessedLot.current_geo_lon.is_(None),
            ProcessedLot.cadastral_number.is_not(None),
            GeoFailure.status == "deferred_no_match",
            GeoFailure.error_message.ilike("%nspd_cadastral%"),
            GeoFailure.error_message.ilike("%no_coordinates%"),
        )
        .order_by(ProcessedLot.id.asc())
        .limit(limit)
    ).all()
    return [int(value) for value in rows]


def managed_recovery_state(
    session: Any,
    *,
    version: str = CAMPAIGN_VERSION,
    sources: Sequence[str] = _DEFAULT_SOURCES,
) -> dict[str, Any]:
    state, _setting = _load_state(session, version=version, sources=sources)
    return state


def run_managed_recovery_batch(
    session_factory: Callable[[], Any],
    *,
    live_result_probe: Callable[[str], tuple[Any | None, str]],
    batch_size: int = 100,
    apply: bool = False,
    version: str = CAMPAIGN_VERSION,
    sources: Sequence[str] = _DEFAULT_SOURCES,
) -> dict[str, Any]:
    """Scan one resumable stale-NSPD batch and persist only the live proven result.

    The cursor advances across normal no-match and validation rejections. If a
    provider call fails operationally, the cursor is held immediately before
    the earliest affected lot so a later run retries that point instead of
    silently skipping it.
    """
    size = int(batch_size)
    if not 1 <= size <= _MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {_MAX_BATCH_SIZE}")
    ordered_sources = tuple(str(value).strip() for value in sources if str(value).strip())
    if not ordered_sources:
        raise ValueError("at least one source is required")
    if len(set(ordered_sources)) != len(ordered_sources):
        raise ValueError("managed recovery sources must be unique")

    with session_factory() as session:
        state, setting = _load_state(session, version=version, sources=ordered_sources)
        source_index = int(state.get("source_index") or 0)
        completed = list(state.get("completed_sources") or [])
        cursors = dict(state.get("cursors") or {})

        selected_ids: list[int] = []
        source: str | None = None
        cursor_before = 0
        while source_index < len(ordered_sources):
            source = ordered_sources[source_index]
            cursor_before = int(cursors.get(source) or 0)
            selected_ids = _eligible_ids(
                session,
                source=source,
                after_id=cursor_before,
                limit=size,
            )
            if selected_ids:
                break
            if source not in completed:
                completed.append(source)
            source_index += 1
            state["source_index"] = source_index
            state["completed_sources"] = completed

        if not selected_ids or source is None:
            state["source_index"] = len(ordered_sources)
            state["completed_sources"] = list(ordered_sources)
            if apply:
                _save_state(session, version=version, state=state, setting=setting)
                session.commit()
            else:
                session.rollback()
            return {
                "status": "completed",
                "version": version,
                "source": None,
                "selected": 0,
                "accepted": 0,
                "updated": 0,
                "operational_failures": 0,
                "state": state,
            }

        result = recover_stale_cadastral_live_results(
            session,
            lot_ids=selected_ids,
            live_result_probe=live_result_probe,
            apply=apply,
        )
        rejections = list(result.get("rejections") or [])
        operational_ids = sorted(
            int(item["lot_id"])
            for item in rejections
            if item.get("reason") == "live_probe_operational_failure"
            and item.get("lot_id") is not None
        )

        cursor_after = cursor_before
        source_completed = False
        if apply:
            if operational_ids:
                cursor_after = max(cursor_before, operational_ids[0] - 1)
            else:
                cursor_after = max(selected_ids)
                source_completed = len(selected_ids) < size
            cursors[source] = cursor_after
            if source_completed:
                if source not in completed:
                    completed.append(source)
                source_index += 1
            state["source_index"] = source_index
            state["cursors"] = cursors
            state["completed_sources"] = completed
            state["probed"] = int(state.get("probed") or 0) + len(selected_ids)
            state["accepted"] = int(state.get("accepted") or 0) + int(result.get("accepted") or 0)
            state["updated"] = int(state.get("updated") or 0) + int(result.get("updated") or 0)
            state["operational_failures"] = (
                int(state.get("operational_failures") or 0) + len(operational_ids)
            )
            _save_state(session, version=version, state=state, setting=setting)
            session.commit()
        else:
            session.rollback()

        status = "retry_required" if operational_ids else "running"
        if not operational_ids and source_completed and source_index >= len(ordered_sources):
            status = "completed"

        return {
            "status": status,
            "version": version,
            "source": source,
            "cursor_before": cursor_before,
            "cursor_after": cursor_after,
            "selected": len(selected_ids),
            "selected_ids": selected_ids,
            "accepted": int(result.get("accepted") or 0),
            "updated": int(result.get("updated") or 0),
            "rejected": int(result.get("rejected") or 0),
            "rejections": rejections,
            "operational_failures": len(operational_ids),
            "source_completed": source_completed,
            "state": state,
        }


def _live_nspd_probe(cadastral: str) -> tuple[Any | None, str]:
    try:
        found = CADASTRAL_GEOCODER._search_nspd_geoportal(cadastral)
    finally:
        time.sleep(0.2)
    if found is None:
        return None, "live_nspd_no_match"
    observed = re.sub(r"\s+", "", str(found.cadastral_number or ""))
    expected = re.sub(r"\s+", "", cadastral)
    if observed != expected:
        return None, "live_nspd_identity_mismatch"
    if found.lat is None or found.lon is None:
        return None, "live_nspd_no_coordinates"
    return found, "exact_match"


def main() -> None:
    parser = argparse.ArgumentParser(description="Resumable live-proven stale NSPD recovery")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--campaign-version", default=CAMPAIGN_VERSION)
    parser.add_argument("--source", action="append")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    result = run_managed_recovery_batch(
        SessionLocal,
        live_result_probe=_live_nspd_probe,
        batch_size=args.batch_size,
        apply=bool(args.apply),
        version=str(args.campaign_version),
        sources=tuple(args.source) if args.source else _DEFAULT_SOURCES,
    )
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str))
    if result["status"] == "retry_required":
        raise SystemExit(75)


if __name__ == "__main__":
    main()
