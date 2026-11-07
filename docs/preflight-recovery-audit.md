# Seven-source fast preflight and safe recovery (draft / read-only)

This PR intentionally does **not** activate a production resume or bypass existing fail-closed controls.

## Audited execution paths

- `.github/workflows/phase3-lite-full-reconcile.yml`: `push` to main can trigger the heavyweight run. Scheduling occurs before a verified backup/restore and free-capacity proof. Existing retry uses `mode=source:<name>`, i.e. a targeted **fresh** source run; it does not prove cursor resume. Multiple successful sources are preserved in final in-memory acceptance but not rolled back/replayed.
- `scripts/run-standard-sources-geo-map-no-vpn.ps1`: SHA and worker-image checks, seven source IDs, pause/circuit, lease, backup and isolated restore verification already exist; no explicit disk/S3 capacity gate before scheduling. Backup verification may be expensive, so lightweight preflight should precede it; verify restore must still finish **before** writes.
- `scripts/resume-partial-standard-geo-map-no-vpn.ps1`: pinned older SHA/Run ID and a specifically accepted six-source partial. **Not** an arbitrary seven-source source-run resumer. It runs GEO/map and must not be repurposed without a new proof of complete coverage.
- `scripts/backup-home-postgres.ps1`: custom-format dump, pg_restore --list, isolated networkless PostgreSQL restore and row-count/schema checks. It writes a local dump and an operations snapshot; it is not a read-only command. Backup-retention eligibility checks metadata and file existence, but does not recompute hashes during retention. A successful restore proves database recovery, not an S3 object-store recovery.

## Required live fast gate (before any queue or heavy crawl)

1. Exact expected 40-hex SHA equals GitHub ref, checkout, all worker-image digests/labels, and currently loaded parser/config revision; reject mutable-image-tag ambiguity.
2. Exactly seven approved source identities with the expected reconcile policies and typed filters. TBankrot must remain excluded and paused. Fail on any source pause, circuit, global block, unexpected source, or invalid source-end contract.
3. Read-only DB connectivity/schema checks, inspect queued/running leases including expired/stale leases. Do not silently reclaim a lease inside a read-only preflight; release/reclaim requires separately approved transactional scheduling.
4. Confirm most recent *verified* backup generation with a revalidated SHA256, restore proof, matching PostgreSQL schema and restore time before live writes. Avoid redoing an expensive restore if the existing proof is fresh, tied to exact backup hash and accepted under policy.
5. Prove free local bytes at backup, PostgreSQL, Docker and log paths, with explicit growth/headroom. Establish S3 quota/available budget via a trusted provider quota API or operator attestation. If quota unknown, **block** rather than inferring free capacity from a successful HEAD or LIST.
6. Only then permit separately approved production execution; never let `--dry-run` call scheduling, backup, GEO, map publication, or DB writes.

## Checkpoint reuse policy

A cursor is **not** a resume contract. Require a durable persisted page boundary, source ID, exact SHA and parser version, canonical filter digest, stable ordering/pagination identity, already-committed results, page boundary inclusivity/exclusivity, and a stable source-end contract. Reject expired cursors, changing filters, changing parser, unknown pagination semantics and reconciliation-enabled resume. Revalidating a checkpoint does not authorize using it: ingestion engine must independently support atomic resume/checkpoint commits. `ingestion.py` is intentionally unchanged; for now failed sources may be retried from start using existing targeted-source scheduling *after* separate authorization. All successful source runs must be accounted for and no failed source may trigger missing-lot reconciliation.

## Dry-run

```bash
python scripts/preflight_source_recovery.py --snapshot /path/to/redacted-readonly-evidence.json
python -m unittest discover -s tests -p test_preflight_source_recovery.py
```

The evaluator takes explicit JSON evidence instead of connecting to services. `allowed` is false when any recovery candidate exists, even if its evidence validates: candidate classification is advisory only. No mock evidence is presented as a live production pass. Capture the snapshot from trusted read-only probes in a separate change; it is unsafe to invent values or default unknown capacity to passing.

## Outstanding implementation / rollout blockers

- Tie read-only evidence collection to the actual home runner, DB, object-store billing/quota endpoint and docker image digest, with credentials redacted.
- Add a gate before the scheduling step in the full workflow and both operator entrypoints; test on a fake Docker/GitHub adapter and isolated filesystem first. Review interactions with other parallel agents before editing shared workflows.
- Test exact-SHA drift, multiple leases, paused sources, backup hash tampering, restore mismatch, disk/S3 exhaustion, cursor expiry, schema revision, fingerprint/filter mutation, partial-source reconciliation and retry idempotency.
- Run the offline suite on CI; this branch has not executed any production ingestion, PostgreSQL write, GEO operation or map publication.
