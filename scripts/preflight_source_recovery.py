"""Read-only, fail-closed decision engine for seven-source recovery.

Consumes an operator-supplied JSON snapshot; never connects to production.
It does NOT execute a resume. A cursor alone is insufficient evidence for safe
reconciliation or source-end completion.
"""
import argparse
import json
import re
import sys
from pathlib import Path

SOURCES = frozenset({
    "torgi.gov.ru", "lot-online.ru", "torgi-russia.ru", "bidexpert.ru",
    "m-ets.ru", "cdtrf.ru", "nistp.ru",
})
NON_RECONCILING = frozenset({"torgi-russia.ru", "m-ets.ru", "cdtrf.ru", "nistp.ru"})
SHA = re.compile(r"^[a-f0-9]{40}$")
DIGEST = re.compile(r"^[a-f0-9]{64}$")


def _require(condition, message, errors):
    if not condition:
        errors.append(message)


def assess(snapshot):
    """Return a deterministic dry-run report; all unknowns block."""
    errors = []
    expected = snapshot.get("expected_sha")
    _require(isinstance(expected, str) and bool(SHA.fullmatch(expected)), "invalid expected_sha", errors)
    _require(expected == snapshot.get("checkout_sha") == snapshot.get("image_sha")
             == snapshot.get("parser_sha"), "exact SHA mismatch", errors)

    sources = snapshot.get("sources")
    _require(isinstance(sources, list) and len(sources) == 7, "expected exactly seven source rows", errors)
    if not isinstance(sources, list):
        sources = []
    names = [s.get("source_id") for s in sources if isinstance(s, dict)]
    _require(len(names) == len(sources) and set(names) == SOURCES
             and len(names) == len(set(names)), "source coverage mismatch or duplicate", errors)
    _require(snapshot.get("db_read_ok") is True, "database read failed", errors)
    _require(snapshot.get("global_network_block") is False, "global network block", errors)
    _require(snapshot.get("active_lease_count") == 0, "active/unknown lease", errors)
    _require(snapshot.get("backup_restore_verified") is True, "restore proof absent", errors)
    _require(snapshot.get("backup_sha256_valid") is True, "backup checksum proof absent", errors)

    for key in ("disk_free_bytes", "disk_required_bytes", "s3_free_bytes", "s3_required_bytes"):
        val = snapshot.get(key)
        _require(type(val) is int and val > 0, key + " missing or invalid", errors)
    if all(type(snapshot.get(x)) is int for x in ("disk_free_bytes", "disk_required_bytes")):
        _require(snapshot["disk_free_bytes"] >= snapshot["disk_required_bytes"],
                 "insufficient disk capacity", errors)
    if all(type(snapshot.get(x)) is int for x in ("s3_free_bytes", "s3_required_bytes")):
        _require(snapshot["s3_free_bytes"] >= snapshot["s3_required_bytes"],
                 "insufficient S3 capacity", errors)

    decisions = {}
    for source in sources:
        if not isinstance(source, dict) or source.get("source_id") not in SOURCES:
            continue
        name = source["source_id"]
        reasons = []
        if source.get("paused") is not False or source.get("circuit_open") is not False:
            reasons.append("source paused/circuit/unknown")
        if source.get("contract_valid") is not True:
            reasons.append("source contract not verified")
        if source.get("status") == "success":
            if source.get("source_end_verified") is not True:
                reasons.append("successful run lacks verified source-end")
            if source.get("items_failed") != 0:
                reasons.append("successful run has failed items")
            if name in NON_RECONCILING and source.get("items_archived") != 0:
                reasons.append("non-reconciling source archived records")
            action = "reuse_success" if not reasons else "block"
        elif source.get("status") in ("failed", "partial"):
            checkpoint = source.get("checkpoint")
            if not isinstance(checkpoint, dict):
                reasons.append("checkpoint absent")
                checkpoint = {}
            for field in ("cursor", "filter_digest", "parser_version", "source_id", "run_sha"):
                if not isinstance(checkpoint.get(field), str) or not checkpoint[field]:
                    reasons.append("checkpoint missing " + field)
            if checkpoint.get("source_id") != name:
                reasons.append("checkpoint source mismatch")
            if checkpoint.get("run_sha") != expected:
                reasons.append("checkpoint SHA mismatch")
            if checkpoint.get("filter_digest") != source.get("current_filter_digest") or not bool(
                DIGEST.fullmatch(str(checkpoint.get("filter_digest", "")))
            ):
                reasons.append("filter digest mismatch")
            if checkpoint.get("parser_version") != source.get("current_parser_version"):
                reasons.append("parser version mismatch")
            # Both explicit provenance and exclusive, monotonic boundaries
            # are mandatory. This remains advisory; the ingestion engine must
            # enforce resume atomically before enabling any actual writes.
            if source.get("cursor_validation_passed") is not True:
                reasons.append("cursor provenance/order not verified")
            if source.get("checkpoint_durable") is not True:
                reasons.append("checkpoint durability not proven")
            if source.get("reconciliation_enabled") is not False:
                reasons.append("cannot resume with reconciliation enabled")
            action = "candidate_only_no_writes" if not reasons else "block"
        else:
            reasons.append("unrecognized source status")
            action = "block"
        decisions[name] = {"action": action, "reasons": reasons}

    _require(len(decisions) == 7, "missing source decision", errors)
    if any(x["action"] == "block" for x in decisions.values()):
        errors.append("source gate blocked")
    return {"allowed": not errors, "dry_run": True, "errors": errors,
            "sources": dict(sorted(decisions.items()))}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path,
                        help="read-only, externally collected JSON evidence")
    args = parser.parse_args(argv)
    try:
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
        report = assess(snapshot)
    except (OSError, ValueError, TypeError) as exc:
        report = {"allowed": False, "dry_run": True, "errors": [str(exc)], "sources": {}}
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["allowed"] else 2


if __name__ == "__main__":
    sys.exit(main())
