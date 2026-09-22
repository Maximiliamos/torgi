# STERDEZ domain migration evidence

Date: 2026-09-22

## Pre-flight

- Target WEB: `https://sterdez.online`.
- Target API: `https://api.sterdez.online`.
- Target WWW behavior: permanent `308` redirect to the apex while preserving path and query.
- Two independent public resolvers return `SERVFAIL` for NS, SOA, A and AAAA.
- Cloudflare diagnostics run `35767459859` used the configured project account and token.
- The Cloudflare API returned no accessible zone named `sterdez.online` (`result: []`).
- No new-domain DNS, Worker route, Pages domain or traffic mutation has been performed.

The external cutover is blocked until the owner confirms the exact domain/ownership and adds the
zone to the project Cloudflare account. If Cloudflare assigns nameservers other than
`titan.ns.cloudflare.com` and `veda.ns.cloudflare.com`, the registrar delegation must be updated
before any traffic switch.

## Active configuration mapping

| Area | Before | Candidate |
|---|---|---|
| Canonical WEB | `dezster.ru` | `sterdez.online` |
| WWW | `www.dezster.ru` | `www.sterdez.online` |
| Canonical API | `api.dezster.ru` | `api.sterdez.online` |
| Cloudflare zone ID | old zone ID embedded in workflow | required repository variable `CLOUDFLARE_CANONICAL_ZONE_ID` |
| Edge Worker routes | old apex/WWW zone | new apex/WWW zone |
| API Worker route | old API hostname | new API hostname |
| CORS | old apex + Pages fallback | new apex + Pages fallback |
| Smoke/Playwright | old canonical hosts | new canonical hosts |
| Source contact URL | old apex | new apex |

The old direct-to-REG.RU cutover workflow and its hard-coded DNS repair script were removed. They
conflicted with the approved Worker-to-healthy-origin architecture and could not restore an exact
per-run BEFORE snapshot during a later manual run. The canonical Cloudflare workflow retains its
own exact snapshot and automatic rollback behavior.

## Data and authentication

- No database schema or data migration is required.
- PostgreSQL, Redis, Photon, ingestion workers and MapDataset are unchanged.
- Session cookies are host-only and `SameSite=Strict`; existing old-domain sessions intentionally
  do not migrate and users will sign in again.
- No MX, SPF, DKIM or DMARC record has been copied or changed because mail usage is not confirmed.
- Historical release evidence retains old-domain URLs intentionally.

## Local verification

- Backend: `334 passed, 12 skipped`.
- Frontend: `73 passed`.
- TypeScript, ESLint and production build: PASS.
- Ruff and Mypy: PASS.
- Edge/API Worker tests: `19 passed`.

## External actions still required

1. Owner confirms spelling `sterdez.online`, ownership and desired DEZSTER/STERDEZ branding.
2. Owner adds or opens the `sterdez.online` zone in the correct Cloudflare account.
3. Cloudflare access token receives the required access to that exact zone.
4. Repository variable `CLOUDFLARE_CANONICAL_ZONE_ID` is set to the verified new zone ID.
5. Mail and old-domain redirect decisions are recorded before changing related records.

No PR, merge, deployment or traffic switch is allowed until the pre-flight and production gates
defined in `10_DEZSTER_переезд_на_sterdez.online_план_и_ТЗ.txt` pass.
