"""Source-spec resolution for scheduled and targeted nationwide ingestion."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from bankrotai.db import LotSyncSourceRun, session_scope
from bankrotai.services.ingestion import default_source_specs, fast_source_specs, source_full_specs
from bankrotai.services.ingestion_incremental import incremental_specs, retry_specs


SEVEN_SOURCE_IDS = frozenset({
    "torgi.gov.ru",
    "lot-online.ru",
    "torgi-russia.ru",
    "bidexpert.ru",
    "m-ets.ru",
    "cdtrf.ru",
    "nistp.ru",
})


def production_seven_specs() -> tuple[Any, ...]:
    specs = tuple(
        spec for spec in default_source_specs()
        if str(spec.source_id) in SEVEN_SOURCE_IDS
    )
    observed = {str(spec.source_id) for spec in specs}
    if observed != SEVEN_SOURCE_IDS:
        missing = sorted(SEVEN_SOURCE_IDS - observed)
        raise RuntimeError(f"Seven-source production spec incomplete: {missing}")
    return specs


def fast_nationwide_source_specs(
    unpaused: Callable[[tuple[Any, ...]], tuple[Any, ...]],
) -> tuple[Any, ...]:
    with session_scope() as session:
        latest_gis = (
            session.query(LotSyncSourceRun)
            .filter_by(
                source_system="torgi.gov.ru",
                status="success",
                complete_source_run=True,
            )
            .order_by(LotSyncSourceRun.finished_at.desc())
            .first()
        )
        overlap_start = (
            latest_gis.finished_at
            if latest_gis and latest_gis.finished_at
            else datetime.now(timezone.utc)
        ) - timedelta(days=1)
    return unpaused(
        fast_source_specs(gis_publish_date_from=overlap_start.date().isoformat())
    )


def resolve_nationwide_specs(
    mode: str,
    unpaused: Callable[[tuple[Any, ...]], tuple[Any, ...]],
) -> tuple[Any, ...]:
    if mode == "fast":
        return fast_nationwide_source_specs(unpaused)
    if mode == "full":
        return unpaused(default_source_specs())
    if mode == "incremental":
        return unpaused(incremental_specs(production_seven_specs()))
    if mode.startswith("source-fast:"):
        source_system = mode.removeprefix("source-fast:")
        specs = tuple(
            spec
            for spec in fast_nationwide_source_specs(unpaused)
            if spec.source_id == source_system
        )
        if not specs:
            raise ValueError(f"Unsupported source-only fast sync: {source_system}")
        return specs
    if mode.startswith("source-incremental:"):
        return retry_specs(
            default_source_specs(), mode.removeprefix("source-incremental:")
        )
    if mode.startswith("source:"):
        return source_full_specs(mode.removeprefix("source:"))
    raise ValueError(f"Unsupported nationwide sync mode: {mode}")


def scheduled_specs(
    mode: str,
    unpaused: Callable[[tuple[Any, ...]], tuple[Any, ...]],
) -> tuple[Any, ...]:
    base = production_seven_specs()
    return unpaused(incremental_specs(base) if mode == "incremental" else base)


def retry_spec_count(source_system: str, source_mode: str) -> tuple[str, int]:
    if source_mode == "fast":
        return f"source-fast:{source_system}", 1
    if source_mode == "incremental":
        specs = retry_specs(default_source_specs(), source_system)
        return f"source-incremental:{source_system}", len(specs)
    if source_mode == "full":
        return f"source:{source_system}", len(source_full_specs(source_system))
    raise ValueError(f"Unsupported source retry mode: {source_mode}")
