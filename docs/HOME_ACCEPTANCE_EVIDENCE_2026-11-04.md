# Home + 22 direct ETP acceptance evidence — 04.11.2026

This file is an audit record, **not** a claim that all 22 sources are operational, accepted or imported. No source may be enabled solely because a preview detected a catalogue.

## 1. Home/Docker/PostgreSQL/backup: live results

[Home health rerun #41822362340](https://github.com/Maximiliamos/torgi/actions/runs/41822362340), attempt 2, checked 2026-11-04T17:11Z: Docker Engine was up, with postgres/redis/photon/api/ingestion/geocode/map containers running, local API live/ready = 200. Geocoder active: most recent batch 2026-11-04T17:08Z. Health still returned **failure** because only two enabled sources were non-operational on freshness (last full source successes >22h old), not because PostgreSQL/worker/map containers were offline.

[Backup/isolated restore rerun #41807377275](https://github.com/Maximiliamos/torgi/actions/runs/41807377275), attempt 2, **success**. New `D:\BankrotAI\dr-backups\bankrotai-20261104-201154.dump`; pg_dump in container 681395001 bytes; checksum `5fa902c32a4e3ab6af4639150fbff0e0fe59e4d36a918390d24887c0a67f7f69`; isolated verification `passed`, schema revision `3dc4e5f6a7b8`, unchanged source during backup. Three verified local generations retained. [#1084](https://github.com/Maximiliamos/torgi/issues/1084) closed after this evidence; #887 encrypted off-host backup remains open.

[Read-only Home sync+map audit #41876277093](https://github.com/Maximiliamos/torgi/actions/runs/41876277093), **success**, with sanitized artifact `home-sync-map-readonly-41876277093`:

- All default source specs: `torgi.gov.ru`, `tbankrot.ru`, `lot-online.ru`, `torgi-russia.ru`, `bidexpert.ru`; runtime active **only GIS Torgi and Lot-online** after pause/circuit filtering.
- 03 Nov 06:00 UTC (09:00 Moscow): `scheduled_full=success`, 2 complete source runs; GIS 1 866 pages/16 896 seen, Lot-online 46 pages/3 091 seen.
- 03 Nov 18:00 UTC (21:00 Moscow): `scheduled_full=success`, 2 complete source runs; GIS 1 898 pages/17 262 seen, Lot-online 47 pages/3 150 seen.
- **No 04 Nov 06:00 UTC attempt in last 12 inspected durable runs**; it was missed during the Home/Docker outage. Celery Beat has a correct UTC 06/18 cron but does not replay missed runs.
- Last 04 Nov current DB MapDataset `20261104T133300074448Z-r6-bundle-s3`, published 13:34:16 UTC, **64 961 points and 80 451 tiles**. REG.RU S3 public manifest checked **verified** on exact same version/point/tile counts. This is DB→published S3 integrity, **not independent browser verification of every lot**.
- No source lease active at time of read-only health; GEO actionable backlog zero with 36 262 deferred-no-match and 1 335 deferred-validation in same dated snapshot. These deferred counts must not be called newly geocoded lots.
- The earlier Home map health said 3 MapDatasets, 1 current; a follow-up workflow validates the previous **ready** MapDataset and S3 manifest before any rollout or rollback claim.

## 2. Live no-VPN ETP readiness (Home)

[22-profile read-only inventory #41649926720, rerun attempt 2](https://github.com/Maximiliamos/torgi/actions/runs/41649926720), 2026-11-04T17:19Z, successfully inspected all **22 profiles**, no DB writes, no GEO/map writes and no archive. Crucial breakdown:

| Reported result | Count | Providers |
|---|---:|---|
| existing connector (not independently assessed) | 2 | M-ETS, Lot-online |
| candidates unverified | 5 | NISTP, etpu.ru, centerr.ru, zakazrf.ru, utpl.ru |
| needs official public catalogue contract | 5 | Roseltorg, Sberbank-AST, AUSIB, uTender, electro-torgi |
| no matches or changed HTML | 7 | Alfalot, Fabrikant, ArbbitLot, Sistematorg, Aukcioncenter, Vertrades, rus-on |
| blocked or parser error | 3 | CDTRF (CdtrfEvidenceError), RTS Tender (HTTPError), ETP Profit (SSLError) |

These statuses are from **one bounded older production inventory implementation**, not the enhanced #1080 parsers. Do not equate a zero candidate with zero lots available. All 21 new direct sources remain **not production-accepted** on 04 Nov. Separate Home read-only pilot smoke and all-source QA are used to evaluate the newer per-detail changes but do not prove a full catalogue.

## 3. PR integration/CI and rollback plan

- #1080 and #1085 started from same main SHA `b474f4ecf9b40fa2bc77a6629bb08fca9e9cfdb7`. The only file changed on both branches was `tests/test_etp_all_sources_campaign.py`; integrated using 19 runnable / 2 blocked from #1080 plus the hard acceptance and GEO/map safety tests of #1085.
- [Integrated draft PR #1086](https://github.com/Maximiliamos/torgi/pull/1086), preserves original two branches unmodified. **Do not merge until exact head CI is green plus live Home/public readiness.**
- Safety: `ACCEPTED_ETP_FOR_AUTOMATIC_INGEST` remains empty, manual all-source import/resume fails closed even with env flags until source-by-source acceptance. Neither the PR nor the new two-page QA performs any production import.
- New `scheduled_sync_catchup.py` handles missed 09/21 Moscow windows if Docker was off; watchdog every 15min, grace 10min, never repeats an already attempted full run, respects PostgreSQL lease and skips recovery within 45min of the next normal slot.
- New `etp_two_pass_acceptance.py` and manual `.github/workflows/etp-two-pass-readonly-acceptance.yml` perform **two independent full read-only passes per single ETP**, only report source-verified last page, stable lot identities, detail identity/price checks, bounded rate and errors. Even an evidence-positive run returns `production_approved=False`: cross-source collision, GEO proof and human review remain independent.
- Existing regru deploy has WEB proxy rollback; MapDataset promoter keeps existing S3 map current until a new immutable version passes validation. Three MapDatasets were observed, but only a previous `ready` version with a **verified public manifest** counts as confirmed rollback readiness. PostgreSQL restore is an emergency-only action; prefer rolling back application SHA first.
- For a release candidate, require exact-SHA Home deploy, successful current-SHA P1/P11, no missing full sync, backup/restore verified, source-level 2-pass evidence, staged 1-source import, then journal/GEO/MapDataset/S3/WEB and rollback drill. No undocumented production writes.

## 4. Remaining exact blockers (not marked complete)

- Fix and verify public API/WEB after earlier 502 and smoke timeouts (#1083, #1082), separate from local API 200.
- Confirm the 04 Nov 21:00 Moscow full scan and future catch-up under deployed code, as 04 Nov 09:00 was absent.
- For each of 21 new platforms, prove *two actual full reads* and last-page/detail/source-independent price/status identity. Home inventory of 5 candidates is **not** this proof.
- Promote only independently accepted source IDs into `default_source_specs`, in small batches with retry/checkpoints, while preserving non-reconciling policy for sources lacking a stable authoritative snapshot.
- Run controlled import only after staging/backups and exact-SHA acceptance; prove individual items in PostgreSQL, geocoder, public S3 manifest, and a real browser. Do not count generic procedure row as verified individual lot.


## 5. User-run Home live parser QA (04 Nov 18:13:12 UTC) and transport evidence

The operator executed in the real `bankrotai-home-ingestion-worker`:
`python -m bankrotai.services.etp_qa_batch --pages 2 --per-page 3 --geo-limit 0`.
The result reported **21/21 profiles attempted**, `qa_only=true`, `complete_source_inventory=false`, `database_writes=0`, `production_map_writes=0`, **78 candidates**, **0 GEO by request**. This is bounded preview rather than complete source acceptance or a 22-source full scan. Breakdown: **14 preview without recorded errors, 1 preview with a partial error, 3 unavailable, 3 blocked**.

The subsequent read-only operator probe yielded exact failures:

| Source | Observed result in deployed Home worker | Action |
|---|---|---|
| `nistp.ru` | Page 1 **33 lot candidates**, `has_next=True`; page 2 throws `SourceSchemaError: no lot links matched ... page 2` | Compare real form method/action/fields and GET-vs-POST result; do not falsely mark page 1 as terminal |
| `ausib.ru` | `SourceAccessError: provider redirect was refused` | Obtain HTTP status and sanitized Location host/path. Only permit explicitly trusted HTTPS official hosts and confirm redirected page content |
| `etp-profit.ru` | `SSLError: CERTIFICATE_VERIFY_FAILED, unable to get local issuer certificate` | Verify public chain, root CA and `requests.certs.where()`; **never use `verify=False`**. Operator webpage indicates transition to two versions of the trading platform from 17 Aug 2026, so confirm correct catalogue |
| `roseltorg.ru` | `ConnectionError: RemoteDisconnected('Remote end closed connection without response')` | Obtain ordinary no-VPN TLS/browser evidence, avoid unsupported anti-bot workarounds; do not treat as a zero-lot success |

Also emitted `XMLParsedAsHTMLWarning` in `common.py:252` during one preview; separate the actual XML response case from ordinary HTML and retain parser diagnostics.

A new **no-write diagnostics module** `src/bankrotai/services/etp_transport_diagnostics.py` has been added to integrated PR #1086 to capture HTTP status, sanitized redirects and NISTP first/second-page form contract without persisting HTML, credentials or personal details. It can be run from the developer worktree via `git show <integration-head>:src/bankrotai/services/etp_transport_diagnostics.py | docker exec -i bankrotai-home-ingestion-worker python -` **without deploying the integration PR**. Its outputs are not yet returned; **no new source is production accepted**.


### Home follow-up: confirmed NISTP GET method and ambiguous AUSIB 302

The operator's second Home probe (4 November) returned:
- NISTP first catalogue HTTP 200, 53 794 bytes, 33 candidates, `nodesearch` form with **method=GET**, 20 next-controls; second page via old code's POST: HTTP 200, 29 004 bytes, 0 candidates, 2 next-controls. Body hashes differ; this is NOT proof of real page 2. **Fix in PR #1086:** respect the form's GET with preserved search parameters, separate regression test. Still needs a live GET probe before declaring the parser fixed in Home.
- AUSIB catalogue HTTP **302** from `https://ausib.ru/etp/trade/list.html`; the sanitized destination has the **same scheme/host/path**, but previous output deliberately removed its query string. Need query **key names only** and safe redirect-contract proof, not a blind `allow_redirects=True`.
- ETP Profit continues `SSLCertVerificationError: unable to get local issuer certificate` with `/usr/local/lib/python3.11/site-packages/certifi/cacert.pem`. Never set `verify=False`.
- Roseltorg: `RemoteDisconnected` (no HTTP status). Public monitoring and official communications reported platform outages/recovery, so one failure cannot be characterized as a parser schema defect.

**No production deploy, database writes, GEO or map activation occurred as a result of this investigation.**


### Operator-confirmed live GET page 2 and AUSIB self-redirect (4 Nov 2026)

A fresh operator-run **read-only** probe in the deployed Home ingestion worker confirmed:

- **NISTP**: `nodesearch` declares `method=GET`; requesting `pagenum=2` with preserved filters returned **HTTP 200**, **50,131 bytes**, **14 parsed individual lots** with the first observed IDs `nistp.ru:502369:1`, `nistp.ru:502367:1`, `nistp.ru:502363:1`. The former POST returned HTTP 200 / 29,004 bytes / **0** parsed lots. This validates the proposed GET-vs-POST code correction in #1086 for the observed page 2, **not** the final page, complete inventory or independent price checks.
- **AUSIB**: `GET https://ausib.ru/etp/trade/list.html` returned HTTP **302** with a Location resolving to **exactly that same complete URL** (identical host/path, zero query keys, `same_full_url=true`). This is a self-redirect loop for that endpoint. Existing fail-closed redirect rejection is justified; it must not be replaced by `allow_redirects=True`. A targeted error classification and regression test were added to #1086; the correct alternative official catalogue contract still needs verifying.
- **No changes to production PostgreSQL, GEO or map** were performed by these diagnostic probes. New ETP automatic ingestion remains disabled until independent full-source acceptance.
