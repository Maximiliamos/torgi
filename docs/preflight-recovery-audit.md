# Seven-source preflight and source recovery — implementation and blockers

## Safety / scope

This draft PR changes only the Phase 3 workflow, the two operator scripts, new read-only validation modules/tests and this audit. `ingestion.py`, source connectors, database schema and GEO/MapDataset implementation are unchanged. No production ingestion, GEO, PostgreSQL mutation, S3 publication or map promotion has been invoked during PR development.

## Implemented

1. **Workflow:** `.github/workflows/phase3-lite-full-reconcile.yml` checks out the exact SHA, verifies the ingestion worker image tag, pipes the checked-in read-only source probe to the running worker, verifies existing backup/disk/S3 quota evidence on the Windows runner, then requires a fresh isolated backup/restore *before* the existing durable run scheduler. All checks fail closed. SHA checks do not claim to prove the actual immutable image digest; this remains a limitation when mutable tags are permitted.
2. **Read-only live source gate:** `scripts/preflight_live_source_gate.py` requires exactly seven operational sources, typed full-catalogue filter contracts, paused TBankrot, no pause/circuit/global block, DB read-only transaction and zero queued/running durable leases (including stale leases). Unknown state blocks.
3. **Backup/disk/S3 gate:** `scripts/preflight_capacity_guard.py` checks the newest local backup's metadata, SHA256 of the *actual dump*, latest successful isolated restore, schema revision and age (24h), map-storage anomaly, local free disk (minimum 10 GiB or 2× dump size) and a signed S3 free/required capacity attestation (maximum 1h old and minimum projected usage 512 MiB).
4. **Manual campaign:** `scripts/run-standard-sources-geo-map-no-vpn.ps1` adds a no-ingestion `-DryRun` and the read-only source probe; for approved real execution it also requires passing backup/disk/S3 capacity checks *before* a fresh restore and durable scheduling. `-DryRun` requires the same read-only source, backup, disk and signed S3 evidence gates as a real campaign; it returns before the fresh backup and any ingestion/GEO/MapDataset writes.
5. **Legacy partial continuation:** `scripts/resume-partial-standard-geo-map-no-vpn.ps1` no longer contains executable GEO/MapDataset publication code and requires explicit SHA and Run ID. It supports diagnostic `-DryRun` only. Six successes and one failed region-filter guard must **never** be treated as seven-source map eligibility.
6. **Recovery planning:** `scripts/preflight_source_recovery.py` classifies previously successful source rows as reusable and failures as blocked or candidate checkpoints only. An apparently valid cursor never authorizes a write; valid candidates still return `allowed=false`. Existing workflow retries only failed sources with a fresh source-specific run; it does **not** resume from arbitrary cursor.

## Capacity evidence contract

Workflow configuration on Windows runner:

- GitHub repository variable `TORGI_S3_CAPACITY_EVIDENCE_PATH`: fully qualified local JSON evidence file produced by a **trusted provider quota collector** (not a human guess).
- GitHub secret `TORGI_CAPACITY_EVIDENCE_HMAC_KEY`: confidential shared verification key, at least 32 characters.
- `D:\BankrotAI\dr-backups`: local verified backup directory; must include a fresh dump + matching metadata.

Evidence JSON fields are `bucket`, `checked_at` (UTC ISO 8601), `free_bytes` (integer), `required_bytes` (integer), `source="provider_quota_api"`, and `hmac_sha256` over the UTF-8 JSON with the first five keys sorted and compact separators. Verify the actual bucket identity against deployment settings **before enabling production**. The signature authenticates who generated the evidence; it cannot prove that quota values were fetched from REG.RU. No trusted REG.RU quota adapter is implemented yet, therefore **do not configure fabricated evidence and do not merge**. Missing evidence blocks ingestion before heavy crawl.

## Checkpoint resume: exact unresolved interface

Existing targeted `mode=source:<source_id>` retries failed sources from the beginning without repeating successful source runs. True page-level resume requires durable fields in each checkpoint: `source_id`, exact `run_sha`, parser version, canonical filter digest, cursor, stable ordering/search snapshot identity, committed page boundary, and source-end proof, plus an ingestion API that atomically verifies these values **in the transaction that claims the lease and resumes**. The existing checkpoint contracts were not verified to persist all these fields; no ability to resume from a cursor is claimed. Implementing this atomically would require changing `ingestion.py` or an agreed delegated resume API and coordinating with PR #1141. Neither change is authorized here.

## Tests and reproducibility

```bash
PYTHONPATH=src pytest -q tests/test_preflight_source_recovery.py tests/test_preflight_live_source_gate.py tests/test_preflight_capacity_guard.py
PYTHONPATH=src pytest -m 'not postgres'
PYTHONPATH=src pytest -m postgres
ruff check src tests
mypy src/bankrotai
python scripts/preflight_source_recovery.py --snapshot path/to/redacted-proof.json
python scripts/preflight_capacity_guard.py --backup-dir path/to/verified-backups --s3-evidence path/to/signed-evidence.json
```

These new tests use synthetic local fixtures and never call a live database/S3. PostgreSQL integration tests belong only on isolated test services. Full workflow tests are not yet proven green; do not treat a queued run as success. The module-size guard and immutable-action checks run in the existing CI. Benchmark results must be measured, not derived from a synthetic fixture or asserted in this document.

## Remaining release blockers

1. A genuinely trustworthy, read-only REG.RU quota collector and independently checked bucket identity; evidence signing without actual provider retrieval is insufficient.
2. Integration tests on the self-hosted Windows runner for the new PowerShell gates and a dry-run with Docker mocks.
3. Exact immutable image digest/attestation, not only an equality of mutable tags.
4. Prove source-run checkpoint provenance/atomic resume contract with the ingestion owner before enabling cursor continuation.
5. Independent review and green CI after current main rebasing or merging under normal branch protection.

The restored backup verifies PostgreSQL data integrity, not restoration of published S3 objects.
