# M-ETS, Alfalot, Sberbank-AST browser observations — 2026-11-06

Observed directly in operator-loaded browser tabs 31–48 after VPN was restored.
No fresh HTTP status/header/payload capture is available. Rendered DOM and public
inline client code prove page structure and visible values, but they do not by
themselves prove a backend HTTP/API contract. No PostgreSQL writes,
reconciliation, archival, GEO or map publication occurred.

## M-ETS

Live no-VPN parser preflight update (2026-11-06): SHA `9f75b5679c8dc5284e7780e2c8e7755d24503b97`
successfully parsed pages 1 and 2 with 20 items each, 40 unique IDs, stable
reported total 3,057 and exact sequential next targets 2 and 3. Raw HTML has
two paginator blocks (upper/lower); both page-1 blocks contained the same
`span.active.startpage` text `1`. The parser now requires every duplicated
paginator to agree on active page and next target. This bounded preflight ended
at the intentional two-page budget and is **not** a terminal-page or two-pass
proof. It performed no database, reconciliation, archival, GEO or map writes.

Tail probe `3b951b88` later observed total 3,082: pages 152, 153 and 154
each contained 20 cards with three distinct canonical-ID hashes. Both pagers
showed the requested page; 152 linked to 153, 153 linked to 154, and full page
154 omitted Next. Thus the UI can hide Next one page before the reported-total
terminal page. Page 155/156 direct evidence remains required before another
full walk.

The subsequent full attempt at SHA `ddb1763b` parsed pages 1–152 and proved
3,040 unique IDs without a duplicate or field-contract failure. The provider
reported 3,059 throughout, but page 152 was full (20 cards) and both pagers
omitted Next. The scanner correctly rejected this as terminal: 19 reported
items were not reached and pass 2 did not start. A bounded pages 152/153/154
tail probe is required before changing pagination logic.

- The selected catalogue URL remains the seven-category search
  `/search?q=eyJzZWFyY2hfY2F0ZWdvcnkiOiIzNiwzNCwzOSwzNywzOCwyOCw0MSJ9`.
  Page 1, page 2 and page 153 were loaded as separate tabs.
- Page 1 and page 2 contain 20 canonical lot links each and have no overlapping
  identities. Page 153 contains 16 links. Its pager contains links 143–152 and
  no page 154/next control, so page 153 is a browser-proven incomplete terminal
  page for this exact filter at the observed time.
- The three observed pages therefore provide 56 distinct identities, not proof
  of a complete automated traversal. A programmatic full walk and second
  independent walk are still required.
- `select[name=stat]` has states 0 announced, 1 accepting applications,
  6 applications ended, 2 auction running, 7 price proposals ended, 8 summing
  up, 3 completed, 4 cancelled and 5 suspended. Only 0 and 1 were selected.
- Selected `search_category` values are 36,34,39,37,38,28,41. Official options
  11 land and 40 agricultural real estate were not selected. Mixed lots can
  still contain land, but that does not prove complete standalone land coverage.
- Exact cards `244381-МЭТС-1` and `244360-МЭТС-1` match their URL identities,
  catalogue titles and initial prices: 266,400 RUB and 6,560,540 RUB. Both
  cards show `Объявленные торги`, application dates, deposit, description,
  category, area/cadastre and a public-offer reduction schedule. The second
  card independently exposes two cadastres and separate building/land areas.
- These two cards prove the scheduled-state layout only. A detail card in
  `Торги в стадии приема заявок` is still required to verify the live current
  price path independently of catalogue text.

### M-ETS full-walk implementation (not live evidence)

- Code commit `74eaa4a12bcc2601afe334445ceffbe9be7c3433` adds
  `etp_mets_full_catalogue.py` and `run-mets-full-readonly-no-vpn.ps1`.
- The scanner verifies on every page the exact seven selected category IDs,
  status IDs 0/1, visible current page, unchanged `offerCount`, sequential
  next link preserving the encoded filter, canonical URL versus
  `trade-МЭТС, Лот №`, positive catalogue price and one of the two selected
  source statuses. It rejects within/cross-page duplicate IDs.
- Completion requires the calculated last page, no next control, an incomplete
  last page, and `unique IDs == offerCount`; two passes must match ID and
  price/status/title record digests. Reports always retain
  `complete_source_inventory=false`, `category_scope_complete=false`,
  `reconcile_missing=false` and `archival_allowed=false`.
- 29 focused M-ETS tests pass; Ruff, focused mypy and PowerShell syntax parsing
  pass. Full repository run: 1,232 passed, 12 skipped, with two unrelated local
  environment failures (stale installed distribution metadata and an existing
  UTC/local-naive lease comparison). The actual no-VPN source run has not yet
  happened and remains `NOT_TESTED`.

## Alfalot

- The operator intentionally selected **`прием заявки`**, not `все`. On both
  loaded search result pages the selected DOM node is exactly
  `<div class="filterItem selected" id="stateFilter_GD">...`.
  Thus `GD` is browser-confirmed as the active-applications UI state.
- The old LotList tabs with `UPCOMING`/`SUPCOMING` are the `Предстоящие торги`
  hall. They show one receivable and a connection-error banner. They are not the
  selected active-applications property inventory and must not be substituted
  for the `GD` search.
- The current search page 2 contains 20 lot links. Page 3 contains 2 different
  links and its pager renders page 3 in `<b>`, with disabled `>` and `>>`; it is
  the browser-proven terminal page. Page 1 was previously visible with 20 links,
  but its exact identity set was not retained, so full 42-ID uniqueness is not
  yet proved.
- Pagination links are only `href="#"`; client code changes the page. A raw,
  sanitized Network request/response is still required before implementing an
  HTTP pagination contract in the backend connector.
- All procedure-type checkboxes remain selected (`BUYING`, `RFI`,
  `SMALL_PURCHASE`, `SALE`, `DEBTOR_SALE`). Therefore the active-state result
  is deliberately mixed and still needs a proven property/sale scope.
- Exact cards `0026026025DS.lot1` and `0042926003DS.lot1` report
  `Состояние лота: Идет прием заявок`, separate procedure/lot numbers,
  application dates and initial prices of 56,000,000 RUB and 197,331,000 RUB.
  The second card also exposes a 4,177.1 m² building and cadastre
  `78:12:0007135:1054`. This proves the detail fields for these two cards, not
  full source coverage.

## Sberbank-AST

- In both actual `ESGoodsList.html` catalogue tabs the checkbox whose value is
  `Продажа имущества (предприятия) банкротов` is still unchecked. Visible cards
  are unfiltered or belong to `Приватизация, аренда и продажа прав`.
- The loaded `SaleBankruptProperty.html` tabs are informational landings, not
  filtered lot result pages. Their presence cannot be treated as an applied
  catalogue filter.
- Public inline code defines `targetPageCode=UtpESGoodsList`, pagenum 0,
  pagesize 21 (options 6/21/51/99), serializes query XML and calls
  `apiRequest('/ESGoodsList','MONITOR','default','','es',...)`. It consumes
  `PurchaseList`, `Total` and BranchGroup aggregation.
- The UI caps `totalRowCount` at 10,000. A visible last page cannot establish
  API completeness above that cap. The currently observed 349 real-estate and
  477 unfiltered pages are not bankruptcy-scope evidence.
- Existing code POSTs JSON to `/api/Processing/main`, but this session did not
  capture the actual bankruptcy-filtered request or response. Filtered page 1,
  page 2, terminal-page and GoodsView detail evidence remain `NOT_TESTED`.

## Remaining acceptance

For all three sources, live two-pass filtered walks (the M-ETS runner is only
implemented/tested so far), expanded representative detail samples, staging
persistence/update/deduplication, backup restore, GEO, MapDataset/S3/website
publication and the 09:00/21:00 MSK cycles remain unproven. Browser pagination
evidence must not enable missing-lot archival.
