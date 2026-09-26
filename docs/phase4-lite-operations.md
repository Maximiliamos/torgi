# Phase 4 Lite production operations and release checklist

Phase 4 Lite keeps operations intentionally small for the current STERDEZ deployment: one home production origin and no more than four application users. The goal is repeatable releases and recovery, not enterprise-scale infrastructure.

## Production topology

- Home Windows PC: PostgreSQL, Redis, Photon, FastAPI, Celery ingestion/GEO/map workers and scheduler.
- REG.RU: public infrastructure and S3 map dataset storage.
- Cloudflare: public WEB/edge entry; map data is not stored in R2.
- Browser map hot path: browser -> REG.RU S3 immutable regional bundles.
- The home origin remains the data authority. REG.RU is not an independent database disaster-recovery replica.

## Release checklist

### Before merge

- Required branch-protection checks are green.
- Database migrations are backward-safe for the currently deployed application during rollout.
- A recent PostgreSQL backup exists and the Phase 3 restore-verification age is within policy.
- No change weakens fail-closed behavior for DB integrity, map publication, backup/restore, GEO liveness or internal ingestion errors.
- Source access/coverage degradation stays diagnostic unless it represents an internal application failure.

### Deployment order

1. Deploy the exact main SHA to the home origin.
2. Verify the home API, database schema and worker/container health.
3. Publish/verify the revision-compatible REG.RU S3 map dataset when the map contract changed.
4. Deploy/verify REG.RU public API path.
5. Deploy/verify Cloudflare WEB/edge path.
6. Run public WEB smoke and production functional reliability checks.

Do not advance a dependent stage when the previous stage is red. Keep the previous known-good map dataset current until a new dataset is completely built and atomically promoted.

### Post-deploy acceptance

A release is accepted when:

- the production home origin reports the exact expected Git SHA;
- /health/live and /health/ready are healthy;
- production reliability is green;
- public WEB smoke is green;
- production functional reliability is green;
- Phase 3 production health is green;
- when ingestion/orchestration changed, a durable full reconciliation reaches terminal success or acceptable partial state and all configured source rows are terminal.

External source access limits, upstream disconnects and coverage guards may produce a source warning. Database-integrity failures, application/internal exceptions, expired leases, backup/restore failures, API/container failures, GEO stalls and map publication failures remain hard failures.

## Rollback rule

Prefer application rollback over database restore.

1. Stop the failed rollout from advancing to later stages.
2. Preserve the current database and take a safety dump before destructive actions.
3. Re-deploy the last known-good application SHA when the schema remains compatible.
4. Keep the last known-good S3 map dataset current until a replacement passes validation.
5. Restore PostgreSQL only when data recovery is actually required, and only from a dump that first passed the isolated restore drill.

Never use an unverified dump directly against the live database.

## Recovery checklist

On the home Windows machine:

- verify the GitHub Actions runner service before restarting it; a broker/network backoff can leave the service process alive while no Worker is accepting jobs;
- verify PostgreSQL and Redis first, then API and Celery workers;
- verify /health/live and /health/ready;
- verify recent backup and restore-drill metadata;
- verify the current map manifest in REG.RU S3;
- verify ingestion lease state before starting a new nationwide sync.

If the runner listener is alive but disconnected and is in a long reconnect backoff, restart only the GitHub Actions runner service. Do not restart PostgreSQL/API/ingestion containers merely to free the runner.

## Backup and restore

Phase 3 policy remains authoritative:

- daily PostgreSQL backup;
- weekly isolated restore drill;
- SHA-256 verification;
- schema-revision and critical row-count plausibility checks;
- restore is tested in an isolated postgres:17 container with no network.

Manual drill:

```powershell
.\scripts\backup-home-postgres.ps1 -Destination 'C:\ProgramData\BankrotAI\dr-backups' -VerifyRestore -RetainDays 14
```

See `docs/phase3-lite-recovery.md` for alert thresholds and recovery details.

## Full reconciliation

The Phase 3 observer is revision-gated: it waits until the exact main SHA is deployed to the home origin.

- If no nationwide sync owns the lease, it creates its own durable full run.
- If an already-active durable full run matches the configured full-source scope, it adopts that exact run ID.
- If a fast/source-only run owns the lease, it waits for release and then creates its own full run.
- Full nationwide tasks use a dedicated 55-minute soft / 60-minute hard Celery time budget.
- The observer waits long enough for that budget and accepts only terminal success or policy-approved partial outcomes.

Never bypass the single-active ingestion lock to make a workflow green.

## Dependency and security gate

- JavaScript dependencies: `npm audit` blocks high/critical findings in CI.
- Python runtime lock: `pip-audit` checks `requirements.lock` in CI.
- Secrets stay in GitHub/host secret storage or environment configuration and are never committed.
- TLS verification remains enabled.
- Security scan failures are fixed or explicitly investigated; they are not converted to warnings merely to unblock a release.
- Root licensing remains an owner decision; do not publish a license implicitly.

## Phase 4 Lite completion criteria

Phase 4 Lite is complete when:

- this runbook and README/roadmap references are current;
- Python and npm dependency audit gates are green;
- release/recovery steps are reproducible from documentation;
- stale historical operational alert issues are closed or classified;
- no open Phase 4 blocker affects a four-user production deployment.
