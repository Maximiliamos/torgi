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
