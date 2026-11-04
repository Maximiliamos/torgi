# ETP parser-stage diagnostic snapshot — 2026-11-05

Source: trusted self-hosted Home runner, [ETP stage checklist run 41915045518](https://github.com/Maximiliamos/torgi/actions/runs/41915045518), artifact `etp-parser-stage-checklist`, timestamp 2026-11-04T21:23 UTC (2026-11-05 00:23 MSK). This is a **two-page / one-detail read-only sample**, not a complete catalogue audit or an ingestion acceptance certificate.

The workflow itself succeeded but **22 of 22 sources reported a first failure/block**. It wrote **zero** production DB records, **zero** map records and archived **zero** lots. Counts below are observed catalogue candidates in the bounded sample; **not saved lots**.

| Source | Pages inspected | Candidate lots | First failure/block | Individual detail stage |
|---|---:|---:|---|---|
| m-ets.ru | 2 | 40 | auction_status: unknown_status_in_sample | PASS |
| cdtrf.ru | 0 | 0 | catalogue_markup: source_schema_or_page_contract, page 1 | NOT_TESTED |
| nistp.ru | 1 | 33 | pagination_next: source_schema_or_page_contract, page 2 | NOT_TESTED |
| alfalot.ru | 1 | 2 | auction_status: unknown_status_in_sample | PASS |
| roseltorg.ru | 0 | 0 | transport: ConnectionError, page 1 | NOT_TESTED |
| sberbank-ast.ru | 2 | 42 | auction_status: unknown_status_in_sample | PASS |
| fabrikant.ru | 1 | 9 | initial_or_current_price: missing_individual_prices_in_sample | PASS |
| arbbitlot.ru | 2 | 40 | initial_or_current_price: missing_individual_prices_in_sample | PASS |
| etpu.ru | 2 | 40 | initial_or_current_price: missing_individual_prices_in_sample | PASS |
| sistematorg.com | 2 | 16 | initial_or_current_price: missing_individual_prices_in_sample | BLOCKED |
| centerr.ru | 2 | 21 | stable_lot_ids: missing_duplicate_or_wrong_source_id, page 2 | NOT_TESTED |
| aukcioncenter.ru | 2 | 30 | initial_or_current_price: missing_individual_prices_in_sample | PASS |
| vertrades.ru | 2 | 40 | initial_or_current_price: missing_individual_prices_in_sample | BLOCKED |
| rus-on.ru | 2 | 40 | initial_or_current_price: missing_individual_prices_in_sample | PASS |
| zakazrf.ru | 2 | 40 | initial_or_current_price: missing_individual_prices_in_sample | PASS |
| utpl.ru | 2 | 40 | initial_or_current_price: missing_individual_prices_in_sample | PASS |
| rts-tender.ru | 0 | 0 | transport: profile_explicitly_blocked | NOT_TESTED |
| etp-profit.ru | 0 | 0 | transport: tls_certificate_validation, page 1 | NOT_TESTED |
| ausib.ru | 0 | 0 | transport: provider_access_or_http, page 1 | NOT_TESTED |
| utender.ru | 0 | 0 | transport: profile_explicitly_blocked | NOT_TESTED |
| electro-torgi.ru | 0 | 0 | transport: profile_explicitly_blocked | NOT_TESTED |
| lot-online.ru | 2 | 160 | individual_detail: ConnectorCapabilityError, page 1 | FAIL |

## Interpretation and priorities

1. **P1 data integrity**: `centerr.ru` repeated or incorrect IDs across pages; `nistp.ru` page-2 schema; `cdtrf.ru` changed catalogue contract. Reject any partial full-scan completion or bulk archiving.
2. **P1 data completeness**: prices and statuses absent from sampled listing records on many sites. A `PASS` detail stage represents just a bounded detail attempt; it **does not** certify individual lot price, status, or source-wide correctness.
3. **P1 transport**: Roseltorg connection, Profit TLS, AUSIB HTTP/redirect, RTS/uTender/Electro-torgi explicit profile block; do not bypass access controls or TLS verification.
4. **P1 Lot-online**: catalogue reads 160 items in sample but the generic diagnostic's `fetch_lot` capability is unsupported. This may be a diagnostic interface limitation rather than a regression in the working production Lot-online connector.
5. **P1 CDTRF separate five-page canary**: [run 41915045547](https://github.com/Maximiliamos/torgi/actions/runs/41915045547) failed with `CdtrfCanaryError: No property candidates; refusing misleading ETP page`.
6. **P1 Torgi Russia**: [recovery run 41907348778](https://github.com/Maximiliamos/torgi/actions/runs/41907348778) recorded circuit `half_open -> closed` after a real regional probe and queued a targeted fast retry; **this does not prove that the queued retry completed or that fresh lots were saved**.

## Acceptance boundaries

Status `PASS` in a two-page sample is only evidence for the checked stage. For production, verify true terminal page, unique lot identity, independently checked detail fields, two complete comparable scans, authorized non-archiving staged import, post-import PostgreSQL/GEO/map validation, and two successful scheduled runs. The automatic allowlist stays closed for unaccepted new ETP sources.

## Observability fixes

[PR #1089](https://github.com/Maximiliamos/torgi/pull/1089) extends durable Home logs to print sanitized per-source stage states, pages/candidate counts and the first failing stage, plus an explicit warning that a green workflow is *not* a green parser. This document is a one-time ground-truth snapshot and must not be treated as live status; the workflow artifact/API is the current data source.

## Torgi Russia browser evidence (2026-11-05)

- User-loaded public catalogue pages 1 and 2 each exposed 24 distinct `/lot/{id}` links.
- Their Next.js flight payloads contained `initialLots` and `initialMeta`; page metadata reported
  `current_page=1/2`, `last_page=209`, `total=5000`, and `per_page=24`.
- The two pages had disjoint visible lot IDs, so the public unfiltered pagination itself was not repeating.
- A cookie notice remained visible on page 2 and an advertising modal was observed, but both sit outside
  the Next.js `initialLots`/`initialMeta` contract consumed by the HTTP parser. They do not require UI clicks.
- Two individual cards exposed distinct IDs, prices, statuses, dates and regions; the apartment card also
  exposed cadastral number `55:36:120305:7503` and area `59.5 sq. m`.
- This does **not** clear production acceptance: the failed production requests use real-estate category and
  regional query parameters. Region-filtered pages 1 and 2 still require captured evidence.

### Region-filtered pagination root cause and fix

- The user opened the verified real-estate query for internal region `1` (Republic of Adygea) and used the
  site's own pagination control to reach page 2. The hydrated page displayed 24 page-2 lots and reported
  85 matching lots in four pages.
- Despite `page=2` in the browser URL, the server HTML still embedded `searchRequest.page=1` and
  `initialMeta.current_page=1`. JavaScript then fetched the real second page from the first-party
  `https://api.торги-россии.рф/api/search` endpoint and replaced the visible list.
- This exactly explains the three production failures after one page: the requests-only connector parsed
  the repeated page-1 SSR payload, and its repeated-page safety guard correctly stopped the run.
- The connector now keeps page 1 on public SSR and uses the observed first-party JSON pagination contract
  for pages 2+, requiring `current_page` to equal the requested page and rejecting invalid, empty advertised,
  or mismatched responses. Missing-lot reconciliation remains disabled.
- The user then followed the site's controls through pages 3 and 4. Region 1 reported 85 lots: pages 1–3
  contain 24 rows and terminal page 4 contains 13 rows. On page 4 both the load-more and next-page controls
  were disabled. Its first and last IDs were `7188706` and `6578770`.
- Both terminal-card samples opened successfully. They exposed distinct prices (`2,985,732` and `1,472,372`
  RUB), statuses, dates, Adygea region and cadastral identifiers (`01:05:2900013:34394` and
  `01:06:2800004:259`). This confirms the terminal-page and detail contracts for region 1 only; it is not yet
  evidence for all 86 region buckets or two independent complete source scans.
