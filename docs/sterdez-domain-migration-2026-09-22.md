# STERDEZ domain migration evidence

Date: 2026-09-22

## Pre-flight

- Target WEB: `https://sterdez.online`.
- Target API: `https://api.sterdez.online`.
- Target WWW behavior: permanent `308` redirect to the apex while preserving path and query.
- Two independent public resolvers return `SERVFAIL` for NS, SOA, A and AAAA.
- Cloudflare diagnostics run `35767459859` used the configured project account and token.
- The first read returned no accessible zone named `sterdez.online` (`result: []`).
- Controlled bootstrap run `35769013654` created zone `2d8fea3305ff3d74777d7fa0a1d9bdb0`.
- Zone status after creation: `pending`.
- Cloudflare assigned `jaime.ns.cloudflare.com` and `raquel.ns.cloudflare.com`.
- Registrar delegation still points to the previous `titan.ns.cloudflare.com` and
  `veda.ns.cloudflare.com`; public DNS therefore remains unavailable.
- Repository variable `CLOUDFLARE_CANONICAL_ZONE_ID` now contains the verified new zone ID.
- No new-domain DNS, Worker route, Pages domain or traffic mutation has been performed.

The external cutover is blocked until the owner changes the `sterdez.online` nameservers in REG.RU
to `jaime.ns.cloudflare.com` and `raquel.ns.cloudflare.com`. No DNS records or routes may be
created until the zone becomes active and two public resolvers return that exact pair.

The bootstrap workflow initially also selected the normal deploy job because its exclusion list did
not yet include the new input. The run was cancelled while it was still verifying the staged origin;
all Pages/Worker deployment steps were skipped. The workflow condition was then corrected so the
zone-only input cannot select the deploy job.

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

1. Owner changes REG.RU nameservers to `jaime.ns.cloudflare.com` and `raquel.ns.cloudflare.com`.
2. Owner confirms desired DEZSTER/STERDEZ branding.
3. Mail and old-domain redirect decisions are recorded before changing related records.

No PR, merge, deployment or traffic switch is allowed until the pre-flight and production gates
defined in `10_DEZSTER_переезд_на_sterdez.online_план_и_ТЗ.txt` pass.
