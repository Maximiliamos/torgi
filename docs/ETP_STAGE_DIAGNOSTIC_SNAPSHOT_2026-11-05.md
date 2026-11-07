# ETP parser-stage diagnostic snapshot — 2026-11-05

Source: latest successful trusted self-hosted Home result available during this review,
[ETP stage checklist run 41950118761](https://github.com/Maximiliamos/torgi/actions/runs/41950118761),
artifact `etp-parser-stage-checklist`, timestamp 2026-11-05 04:52 MSK. This is a
**two-page / one-detail read-only sample**, not a complete catalogue audit or an ingestion
acceptance certificate. Later source-code fixes are described below but are not promoted to
`PASS` until a new exact-SHA live artifact verifies them.

The workflow itself succeeded but **22 of 22 sources reported a first failure/block**. It wrote **zero** production DB records, **zero** map records and archived **zero** lots. Counts below are observed catalogue candidates in the bounded sample; **not saved lots**.

## GitHub state checked on 2026-11-05

- #1089 (actionable per-source diagnostics), #1093 (NISTP GET pagination and CenterR IDs),
  and #1103 (NISTP shared-card per-lot detail) are merged into `main`.
- #1098 (this checklist v2) and #1105 (NISTP filtered full-catalogue proof) remain open;
  #1086 remains a Draft integration PR.
- The three checklist workflow events after run 41950118761 were `skipped`, so they provide no
  newer 22-source artifact. The matrix below therefore remains the latest common live baseline.
- A merged code fix or a browser capture is recorded as subsequent evidence, but it does not
  silently replace an old live `FAIL`/`NOT_TESTED` with `PASS`.

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

## Complete stage matrix from run 41950118761

Each slash-separated cell follows the order in its heading. `PASS` applies only to the bounded
observation represented by the artifact. In particular, `PASS` for page 2 is not proof of the
last page, and `PASS` for one detail is not source-wide field coverage.

| Source | HTTP/catalogue | Lots/ID | Page 2/end | Detail/ID | Price/status/class | DB/GEO/map/09-21 | First failure |
|---|---|---|---|---|---|---|---|
| `m-ets.ru` | PASS/PASS | PASS/PASS | PASS/NOT_TESTED | PASS/PASS | PASS/FAIL/PASS | NOT_TESTED/NOT_TESTED/NOT_TESTED/NOT_TESTED | auction_status: unknown_status_in_sample |
| `cdtrf.ru` | NOT_TESTED/FAIL | NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED/NOT_TESTED/NOT_TESTED | catalogue_markup: source_schema_or_page_contract |
| `nistp.ru` | PASS/PASS | PASS/PASS | FAIL/NOT_TESTED | NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED/NOT_TESTED/NOT_TESTED | pagination_next: source_schema_or_page_contract |
| `alfalot.ru` | PASS/PASS | PASS/PASS | NOT_TESTED/NOT_TESTED | PASS/PASS | PASS/FAIL/FAIL | NOT_TESTED/NOT_TESTED/NOT_TESTED/NOT_TESTED | auction_status: unknown_status_in_sample |
| `roseltorg.ru` | BLOCKED/NOT_TESTED | NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED/NOT_TESTED/NOT_TESTED | transport: transport_ConnectionError |
| `sberbank-ast.ru` | PASS/PASS | PASS/PASS | PASS/NOT_TESTED | PASS/PASS | PASS/FAIL/FAIL | NOT_TESTED/NOT_TESTED/NOT_TESTED/NOT_TESTED | auction_status: unknown_status_in_sample |
| `fabrikant.ru` | PASS/PASS | PASS/PASS | NOT_TESTED/NOT_TESTED | PASS/PASS | FAIL/FAIL/PASS | NOT_TESTED/NOT_TESTED/NOT_TESTED/NOT_TESTED | initial_or_current_price: missing_individual_prices_in_sample |
| `arbbitlot.ru` | PASS/PASS | PASS/PASS | PASS/NOT_TESTED | PASS/PASS | FAIL/PASS/PASS | NOT_TESTED/NOT_TESTED/NOT_TESTED/NOT_TESTED | initial_or_current_price: missing_individual_prices_in_sample |
| `etpu.ru` | PASS/PASS | PASS/PASS | PASS/NOT_TESTED | PASS/PASS | FAIL/PASS/FAIL | NOT_TESTED/NOT_TESTED/NOT_TESTED/NOT_TESTED | initial_or_current_price: missing_individual_prices_in_sample |
| `sistematorg.com` | PASS/PASS | PASS/PASS | PASS/NOT_TESTED | BLOCKED/NOT_TESTED | FAIL/FAIL/PASS | NOT_TESTED/NOT_TESTED/NOT_TESTED/NOT_TESTED | initial_or_current_price: missing_individual_prices_in_sample |
| `centerr.ru` | PASS/PASS | PASS/FAIL | NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED/NOT_TESTED/NOT_TESTED | stable_lot_ids: missing_duplicate_or_wrong_source_id |
| `aukcioncenter.ru` | PASS/PASS | PASS/PASS | PASS/NOT_TESTED | PASS/PASS | FAIL/FAIL/FAIL | NOT_TESTED/NOT_TESTED/NOT_TESTED/NOT_TESTED | initial_or_current_price: missing_individual_prices_in_sample |
| `vertrades.ru` | PASS/PASS | PASS/PASS | PASS/NOT_TESTED | BLOCKED/NOT_TESTED | FAIL/FAIL/FAIL | NOT_TESTED/NOT_TESTED/NOT_TESTED/NOT_TESTED | initial_or_current_price: missing_individual_prices_in_sample |
| `rus-on.ru` | PASS/PASS | PASS/PASS | PASS/NOT_TESTED | PASS/PASS | FAIL/PASS/PASS | NOT_TESTED/NOT_TESTED/NOT_TESTED/NOT_TESTED | initial_or_current_price: missing_individual_prices_in_sample |
| `zakazrf.ru` | PASS/PASS | PASS/PASS | PASS/NOT_TESTED | PASS/PASS | FAIL/PASS/FAIL | NOT_TESTED/NOT_TESTED/NOT_TESTED/NOT_TESTED | initial_or_current_price: missing_individual_prices_in_sample |
| `utpl.ru` | PASS/PASS | PASS/PASS | PASS/NOT_TESTED | PASS/PASS | FAIL/PASS/FAIL | NOT_TESTED/NOT_TESTED/NOT_TESTED/NOT_TESTED | initial_or_current_price: missing_individual_prices_in_sample |
| `rts-tender.ru` | BLOCKED/NOT_TESTED | NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED/NOT_TESTED/NOT_TESTED | transport: profile_explicitly_blocked |
| `etp-profit.ru` | BLOCKED/NOT_TESTED | NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED/NOT_TESTED/NOT_TESTED | transport: tls_certificate_validation |
| `ausib.ru` | BLOCKED/NOT_TESTED | NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED/NOT_TESTED/NOT_TESTED | transport: provider_access_or_http |
| `utender.ru` | BLOCKED/NOT_TESTED | NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED/NOT_TESTED/NOT_TESTED | transport: profile_explicitly_blocked |
| `electro-torgi.ru` | BLOCKED/NOT_TESTED | NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED/NOT_TESTED | NOT_TESTED/NOT_TESTED/NOT_TESTED/NOT_TESTED | transport: profile_explicitly_blocked |
| `lot-online.ru` | PASS/PASS | PASS/PASS | PASS/NOT_TESTED | FAIL/NOT_TESTED | PASS/PASS/PASS | NOT_TESTED/NOT_TESTED/NOT_TESTED/NOT_TESTED | individual_detail: unexpected_ConnectorCapabilityError |

## Subsequent source evidence (kept separate from the common artifact)

| Source | Newly completed evidence | Still not completed |
|---|---|---|
| `nistp.ru` | #1093 merged the real GET pagination contract with repeated checked filters; #1103 merged isolated per-lot parsing for shared trade cards. User-loaded catalogue/card evidence confirmed composite IDs, individual price/status and non-property rows without treating them as real estate. The active catalogue filter is explicitly limited to `Торги объявлены`, `Прием заявок`, `Идут торги`; the browser showed 143 pages. | #1105 is still open and no successful exact-SHA two-pass active-catalogue report has been attached yet. Therefore `last_page_proven`, `two_pass_catalogue_match`, PostgreSQL/GEO/map/web and both schedules remain `NOT_TESTED`. No mass archival is allowed. |
| `centerr.ru` | #1093 merged exclusion of repeated news/sidebar links from catalogue lot IDs, with regression tests. | No newer Home artifact has re-run the real source after the merge, so the old live ID failure is not promoted to `PASS`. Full pagination, details, prices and downstream stages remain `NOT_TESTED`. |
| `cdtrf.ru` | User-loaded catalogue and direct API pages on 2026-11-05 confirmed the public `GET /api/Trade/trades` contract. Pages 1/2 had unique `tradeLotId` values with zero overlap and matching catalogue ID/price/status. Separate filters returned 660 `Declare` and 9,815 `RecieveReq` lots. During observation the combined total changed from 10,472 to 10,475; page 524 was the real terminal page with 15 rows and matching UI/API IDs. Details independently matched `tradeId/tradeLotId` pairs `392267/392802` and `392252/392787`, including price, status, address, cadastral number, area and dates. Adding verified land category `41` increased the selected property catalogue from 3,089 to 6,353 lots and restored pure-land lot `392787`; the former filter omitted 3,264 rows. For the corrected filter, pages 1/2 contained 100 disjoint IDs each and terminal page 64 contained 54 disjoint IDs when `totalCount` grew to 6,354; no sampled row lacked a price. The terminal rows were `DefinePart`/`Определение участников`. The parser now retains SSR `location` and an unambiguous square-metre area. | Page 64 is proven for this observation, but intermediate pages 3–63 and a second complete pass are not. Home runs 42095458543 and 42095914920 proved that CDTRF is unreachable while VPN is enabled; both failed before page 1 with transport timeouts and wrote no evidence/DB data. A local no-VPN two-pass script now prints page-by-page progress, rejects overlaps/coverage gaps and compares digests. PostgreSQL dedupe/update, GEO/map/web and schedules remain `NOT_TESTED`. No reconciliation or archival is allowed. |
| Other 19 registered ETP entries | No newer qualifying common Home artifact was found after run 41950118761. Their bounded statuses remain exactly as recorded in the matrix above. | Any code-only fix or isolated browser view still needs a fresh exact-SHA source run and the applicable acceptance stages. |

`torgi-russia.ru` is a separate production source rather than one of these 22 registered direct-ETP
entries. Its browser/API evidence and remaining production blockers are recorded in the dedicated
appendix below; it must not be inserted as a fake 23rd row or substituted for an ETP.

## Coverage audit against the required 12 stages

| Required stage | Current machine-readable coverage after checklist v2 | Remaining gap |
|---|---|---|
| 1. DNS, HTTPS, HTTP, redirects | DECLARED: separate `dns_resolution`, `tls_certificate`, `http_response`, `redirect_chain`, plus `catalogue_url`/`transport` | Bounded connector runs do not yet populate every separate observation |
| 2. Correct catalogue and markup | YES: `catalogue_markup`, source contract and CDTRF JSON probe | Requires a fresh live run after each contract change |
| 3. JavaScript, cookie/tutorial/filter UI | DECLARED: `browser_javascript`, `cookie_or_tutorial_modal`, `catalogue_filter_state`; HTTP-only UI stages use N/A only when genuinely inapplicable | Browser-backed sources still need explicit UI/filter evidence |
| 4. Lot discovery and procedure/lot separation | DECLARED: `lot_discovery`, `procedure_lot_separation`, `lot_id_provenance` | Procedure-to-lot-to-asset isolation is not proven for every source |
| 5. Unique IDs and duplicates | YES for bounded samples: `stable_lot_ids`, `lot_id_provenance` | Full-catalogue and PostgreSQL uniqueness are not yet proven |
| 6. All pages and terminal proof | DECLARED: `pagination_next`, `pagination_page_contract`, `last_page_proven`, `two_pass_catalogue_match` | Two independent complete scans and filter-partition coverage are still missing for unaccepted sources |
| 7. Individual cards | YES for requested probes: `individual_detail`, `detail_identity` | A one-card probe is not source-wide coverage |
| 8. Price, status, address, cadastre, area, dates | DECLARED: separate detail price/status/address/cadastral/area/date stages | Most sources still lack source-wide live field evidence |
| 9. Catalogue/detail comparison | DECLARED: separate price/status/address/cadastral/area/date consistency stages | Multi-asset isolation and fresh-card comparisons remain incomplete |
| 10. PostgreSQL save and update | DECLARED: backup/restore, import, insert, update, dedupe and mass-archival guard stages | All intentionally remain `NOT_TESTED` until an authorized staged import |
| 11. Geocoding and map publication | DECLARED: GEO, MapDataset, map/S3 publication, website visibility, lot links and Sources filter | PostgreSQL-to-Web counts still require end-to-end proof |
| 12. Scheduled 09:00 and 21:00 MSK runs | DECLARED: separate 09:00, 21:00 and two-consecutive-cycle stages | Record both slots, exact SHA, retries and two consecutive successful cycles |

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
- The first exact-SHA production reconciliation after this change failed closed on region 1 page 2 with
  HTTP 405 because the connector used `GET`. A no-VPN browser DevTools capture then proved the actual
  contract: `POST https://api.торги-россии.рф/api/search`, JSON body with `search`, `history_only`,
  `categorie_childs`, `regions`, and `page`, plus JSON `Accept`/`Content-Type`. The connector now reproduces
  that request instead of encoding filters into the query string. The failed run saw 23 existing page-1 rows,
  changed none, and archived none; `reconcile_missing=False` remains mandatory until two complete passes.
- The next exact-SHA run proved HTTP POST but failed closed with `requested=2 observed=0`: the connector
  expected pagination beside top-level `data`. Two fresh no-VPN browser tabs exposed the actual hydrated
  contract as Laravel-style `data` plus nested `meta`. Region 1 page 2 reported
  `meta.current_page=2`; page 4 reported `meta.current_page=4`, `meta.last_page=4`, began at row 73,
  rendered 13 distinct lots, and disabled both continuation controls. The parser now validates and consumes
  nested `meta` while retaining the wrong-page guard and non-archiving recovery policy.
