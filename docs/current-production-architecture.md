# Current STERDEZ production architecture (2026-10-08)

**Canonical operational overview.** Use this with [Phase 4 operations](phase4-lite-operations.md),
[Phase 3 backup/recovery](phase3-lite-recovery.md) and [production configuration](production-configuration.md).
Historical Neon deployment guides and dated release reports are not current runbooks.

## Data authority and web delivery

- **Home Windows origin** is the single authoritative running PostgreSQL database and
  processes API, Redis, local Photon, ingestion/GEO/map workers and Celery beat.
- **REG.RU** provides public API routing and immutable object storage for published
  regional S3 map bundles/manifests; it is **not** an independently restorable
  production PostgreSQL replica. Home→REG.RU currently uses WSS/wstunnel relay.
- **Cloudflare** hosts the public WEB/edge path for `sterdez.online`.
  WEB can remain visible while Home is unavailable, but ingestion, GEO and many
  API operations cannot be considered healthy or fresh.
- On publication, a MapDataset version must pass DB→dataset→S3 quality checks
  and be atomically promoted; a failed build must leave the last verified
  map release intact. The dedicated P1/P11 workflows are the production
  acceptance evidence, not a CI unit test or an uncorrelated deployment.
- Current workflow name `Deploy home secondary origin` is **legacy naming** for
  the actual authoritative Home deployment. Do not rename it without also
  updating every exact-SHA workflow dependency. Likewise `KOYEB_SERVICE_KEY`
  is a retained secret key name; rename only after coordinated replacement.

## Sources and public product scope

The default connector specifications include `torgi.gov.ru`,
`lot-online.ru`, `torgi-russia.ru` and `bidexpert.ru`. Effective
nationwide sources are runtime-filtered by explicit source pauses and circuit
state, so a paused source **never proves production coverage** merely because
a complete reconciliation succeeds. TBankrot is an isolated authenticated
source and paused by default.

The intended **public-map** scope is only current sales of real estate.
P13–P17 integrity changes and the owner-provided examples are tracked in
[ROADMAP](ROADMAP.md) and PR #932. Until accepted on the exact production
SHA and verified against live DB→MapDataset→S3→browser, do not claim that
historical closed/rental/transport GEO errors have been eliminated.

## Backup and disaster recovery

- Canonical local PostgreSQL backup location: `D:\BankrotAI\dr-backups`.
- Daily schedule checks whether a verified backup is due; successful new
  backups are targeted approximately every **48 hours**.
- Only restore-verified SHA-256/size/schema-matched `.dump + .json` pairs
  count toward the target of **three** retained generations.
- A replacement backup is only eligible to prune older verified backups
  after its isolated `postgres:17` restore succeeds. Critical health
  threshold for the last usable backup/restore evidence: **60 hours**.
- These are **local** recoverability measures, not confirmed host-loss DR.
  Encrypted off-host export remains opt-in pending owner-approved bucket,
  independent key escrow and a real restoration on replacement infrastructure.
  See [off-host DR plan](offhost-encrypted-dr.md).

## Safe release sequence

1. Require `main` exact SHA and successful Home deployment.
2. Deploy and verify REG.RU API/WEB staging and same SHA; then verify Cloudflare
   canonical path and public WEB smoke.
3. Require P1 and P11 (including full soak) on that same SHA; full reconciliation
   is separate evidence and disabled sources do not count toward coverage.
4. Publish a versioned release with SBOM/checksums and verified OIDC/Sigstore
   provenance. Never claim the next patch version is released before its own
   exact-SHA acceptance.
5. Manual `workflow_dispatch` Home-ordering hardening is included in draft
   PR #932 and is **not yet active** until that PR is merged and accepted.

**Safety:** Never run the old manual named Cloudflare Tunnel workflow to
repair WSS relay without a separately approved recovery plan. It has its
own explicit opt-in gate in draft PR #933.
