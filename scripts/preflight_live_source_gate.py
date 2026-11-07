"""Read-only live source eligibility probe for the home ingestion container.

Run by piping this script to `docker exec -i ... python -`. Never schedules
a source run, reclaims a lease, changes state, or writes a checkpoint.
"""
import json
import sys

EXPECTED = frozenset({
    "torgi.gov.ru", "lot-online.ru", "torgi-russia.ru",
    "bidexpert.ru", "m-ets.ru", "cdtrf.ru", "nistp.ru",
})
TYPED = {
    "m-ets.ru": "MetsSyncFilters",
    "cdtrf.ru": "CdtrfSyncFilters",
    "nistp.ru": "EtpSearchFilters",
}


def validate(specs, paused, circuit, globally_blocked, active_runs, db_ok):
    """Fail closed on incomplete, paused or unexpected configuration."""
    errors = []
    sources = [s.source_id for s in specs]
    if len(sources) != 7 or set(sources) != EXPECTED:
        errors.append("expected seven exact and unique operational sources")
    if any(paused(name) or circuit(name) for name in EXPECTED):
        errors.append("source paused or circuit open")
    if globally_blocked is not False:
        errors.append("global source network block is active or unknown")
    if active_runs:
        errors.append("queued/running durable run already exists")
    if db_ok is not True:
        errors.append("database read-only probe not proven")
    for spec in specs:
        name = spec.source_id
        if name in TYPED and (
            type(spec.filters).__name__ != TYPED[name]
            or spec.reconcile_missing is not False
            or spec.require_source_end is not True
            or spec.max_batches != 500
        ):
            errors.append("invalid full-catalogue source contract: " + name)
    return {"pass": not errors, "errors": errors, "configured": sorted(sources)}


def live_probe():
    from sqlalchemy import select, text

    from bankrotai.db import LotSyncRun, SessionLocal
    from bankrotai.services.ingestion import default_source_specs
    from bankrotai.tasks import (
        _global_source_network_blocked,
        _source_is_circuit_blocked,
        _source_is_paused,
    )

    with SessionLocal() as db:
        # Explicit read-only transaction; do not reclaim even expired leases.
        db.execute(text("SET TRANSACTION READ ONLY"))
        active = list(db.scalars(select(LotSyncRun.id).where(
            LotSyncRun.status.in_(("queued", "running"))
        )))
        result = validate(
            [s for s in default_source_specs() if s.source_id != "tbankrot.ru"],
            _source_is_paused, _source_is_circuit_blocked,
            _global_source_network_blocked(), active, True,
        )
        if not _source_is_paused("tbankrot.ru"):
            result["errors"].append("tbankrot.ru must remain paused")
            result["pass"] = False
        return result


def main():
    try:
        result = live_probe()
    except Exception as exc:
        result = {"pass": False, "errors": [
            "read-only live probe failed: " + type(exc).__name__ + ": " + str(exc)
        ]}
    print("SOURCE_PREFLIGHT=" + json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["pass"] else 2


if __name__ == "__main__":
    sys.exit(main())
