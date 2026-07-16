# Production audit remediation status — 2026-07-17

This document tracks the static-audit findings against the implementation branch.

| # | Finding | Status | Remediation |
|---|---|---|---|
| 1 | Unsafe deployment defaults | Fixed | Required non-default secrets, authenticated Redis, no host publication for DB/Redis/API, loopback WEB binding by default, authenticated WEB ingress. |
| 2 | TLS verification disabled | Fixed | Production always verifies TLS; custom CA is supported explicitly. |
| 3 | AI false precision | Fixed | Preliminary-analysis wording, strict schemas/ranges/links, no hard-coded valuation floors, untrusted-data prompt boundary, forced human review. |
| 4 | Weak API authentication/rate limiting | Fixed | Production fails closed, GET is protected, Redis-backed shared rate limit, DB/Redis/config readiness. |
| 5 | Cross-source identifier collisions | Fixed | Composite unique constraints and source-aware upserts through Alembic. |
| 6 | Competing migration mechanisms | Fixed | Runtime initialization uses Alembic; `create_all()` remains test-only. |
| 7 | Plaintext secrets/provider switching | Fixed | Secret settings cannot enter `app_settings`; fallback is opt-in and provider provenance is recorded. |
| 8 | Daemon-thread task fallback | Fixed for production | Production returns 503 when the queue is unavailable; local fallback remains explicit for desktop development. |
| 9 | Architectural monoliths | Partially remediated | AI contracts and scraper contracts/extractors are separate modules. Further GUI screen/controller decomposition is a longer refactor and should be done incrementally with UI regression coverage. |
| 10 | CI did not prove the branch | Fixed locally/CI configured | Workflow covers lint, types, SQLite, migrations, PostgreSQL, WEB, Docker and Playwright using lock files. Repository branch protection remains an owner-side GitHub setting. |
| 11 | Silent heuristic extraction | Fixed | Price/area extraction returns value, source fragment, rule ID, confidence and warnings; ambiguous unlabeled values are rejected. |
| 12 | Non-reproducible/root containers | Fixed | Runtime/dev locks are committed; API and WEB runtime processes execute as non-root users. |
| 13 | Stale documentation | Fixed | README describes current dependencies, migration, queue, security and validation behavior; this dated status replaces embedded stale audit text. |
| 14 | Missing public-product process | Partially remediated | `SECURITY.md` and `CHANGELOG.md` added. Publishing releases/advisories and enabling private reporting/branch protection require repository-owner actions on GitHub. |

No AI output is suitable as an independent appraisal or investment decision.
Production deployment still requires operator-managed TLS termination, backups,
monitoring, secret rotation, and release/rollback procedures.
