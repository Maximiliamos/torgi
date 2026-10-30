# ETP source adapters

This package adds read-only pilot connectors for the ETP captures made on
2026-10-29.  It deliberately does not add them to `default_source_specs()`:
none of the captures proves a complete source inventory, so missing lots must
not be reconciled or archived yet.

## Contract

- All connectors implement `AuctionConnector.search() -> ConnectorPage`.
- Proven detail routes implement `fetch_lot()` and parse labelled prices,
  cadastral numbers, VIN, procedure/case identifiers, deadlines and status.
- Stable external IDs are derived from provider lot/detail URLs, not titles.
- Unknown prices and statuses remain `None` / `unknown`.
- `complete_source_inventory=false`, `safe_for_archival=false`, and
  `reconcile_missing=false` are carried in metadata.
- HTTP 401, 403 and 429, timeouts, and schema drift are explicit failures.
- Blocked sources report `ConnectorHealth(status="blocked")`; they never return
  a misleading successful empty page.

## Capture validation matrix

The `parsed` column is the number of unique items produced from the saved
`catalogue_page_1/page.html` fixture.  These are archive validation results,
not live/full-inventory claims.

| source | captured state | parsed | pagination |
|---|---:|---:|---|
| m-ets.ru | catalogue + details | 20 | GET `page` |
| cdtrf.ru | catalogue + details | 20 | GET `PageNum` |
| nistp.ru | catalogue + details | 20 | form POST `pagenum` |
| alfalot.ru | catalogue + details | 2 | provider-specific form |
| roseltorg.ru | catalogue + details | 10 | not proven |
| sberbank-ast.ru | catalogue only | 21 | public JSON API `pagenum` |
| fabrikant.ru | catalogue + details | 7 | GET `page_number` |
| arbbitlot.ru | catalogue + details | 20 | ASP.NET postback |
| etpu.ru | catalogue + details | 20 | ASP.NET postback |
| sistematorg.com | catalogue + details | 20 | form POST `pagenum` |
| centerr.ru | catalogue + details | 21 | ASP.NET postback |
| aukcioncenter.ru | catalogue + details | 15 | GET `page` |
| vertrades.ru | catalogue + details | 20 | GET `page` |
| rus-on.ru | catalogue + details | 20 | form POST `pagenum` |
| zakazrf.ru | catalogue + details | 20 | ASP.NET postback |
| utpl.ru | catalogue + details | 20 | ASP.NET postback |
| rts-tender.ru | navigation error | blocked | not proven |
| etp-profit.ru | not a catalogue | blocked | not proven |
| ausib.ru | no catalogue found | blocked | not proven |
| utender.ru | navigation error | blocked | not proven |
| electro-torgi.ru | access denied | blocked | not proven |

`lot-online.ru` is intentionally absent: the repository already has its
dedicated production connector, which remains registered unchanged.

## Promotion work

Before enabling any profile in scheduled ingestion, prove the last page and
page-size contract, add source-specific detail parsing, compare two complete
runs, and explicitly decide whether `reconcile_missing` is safe.  The shared
ASP.NET engines require provider-specific postback state handling rather than
invented GET pagination.


## Temporary manual QA mode (31 October 2026)

The initial source discovery is *not* a release gate or production import. P1
and P11 workflows retain their historical push/schedule triggers for
compatibility, but their long worker jobs are temporarily gated to
**workflow_dispatch only**. Automatic QA-branch releases show skipped
P1/P11 jobs instead of running lengthy production checks. An operator can
still manually launch the full tests in Actions. Restore the original job
conditions after the source-by-source QA campaign is accepted.

The automatic Home deploy completion triggers
`.github/workflows/etp-manual-qa-all-sources.yml`, which calls the trusted
Home worker's `python -m bankrotai.services.etp_qa_batch`.
All 21 new direct profile configurations are attempted; blocked providers
produce a diagnostic row rather than fictitious data. No rate-limit bypass.
The run publishes a **private GitHub artifact** with per-provider counts,
errors and exact-cadastre geocode counts, not a public production map.

In Sterdez.online, an authenticated **admin** opens the normal map and chooses
**Тестовая карта ЭТП**. Choose an ETP and click
**Получить лоты и геокодировать**. The API
`GET /api/etp/qa-map/{source_id}` fetches no more than two
catalogue pages, three listed candidates per page, and two exact-cadastre
geocodes. It returns the **source URL, external ID, candidate fields, geo
status**, and only verified coordinates. Missing GEO stays visible in the
source list but is not placed at a guessed location.

The QA layer is **separate from production PostgreSQL/S3 tiles**. It does not
write bids, lots, journal, duplicate groups, map datasets or archive statuses.
The source/record display says `QA / не проверено`; it is not evidence of
full paging, full inventory or current auction status. It must not be used to
make investment or bid decisions until detail verification passes.

For each provider, manually record: missing lot IDs; incorrect titles,
cadastral numbers, price/date/status; pagination; duplicates; broken official
links; missing geo; blocked HTTP/network; discrepancies against the official
lot card. Re-enable P1/P11 automatic job execution and production import only after
individual sources are approved and their final-page + status/price update
contracts are tested. The existing atomic canary mechanism and archive guard
remain independent of QA mode.
