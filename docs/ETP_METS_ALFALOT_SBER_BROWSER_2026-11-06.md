# M-ETS, Alfalot, Sberbank-AST browser observations — 2026-11-06

Observed directly in operator-loaded browser tabs 31–42 after VPN was restored.
No fresh HTTP status/header/payload capture is available. DOM and public inline
client code are evidence of page structure, not proof of a successful API request.
No source acceptance, database writes, archival, GEO or map publication occurred.

## M-ETS

- Tabs 31/32/33 all have the same category-only `/search?q=...` URL, without
  `page=2`. Each contains the same 20 lot links (20/20 overlap).
  This does not prove a parser pagination failure or provider redirect: the
  navigation history/network needed to explain the lost parameter was not captured.
- The UI reports 3,056 results, links to page 2 and last page 153; neither
  page 2 nor page 153 is currently loaded/proven.
- Card links are relative strings such as `244381-1`, not root-relative paths.
  Resolve them against the official source URL before checking their identity.
- `select[name=stat]`: 0 announced, 1 accepting applications, 6 applications
  ended, 2 auction running, 7 price proposals ended, 8 summing up, 3 completed,
  4 cancelled, 5 suspended. Currently 0 and 1 selected; this is filter evidence,
  not independent proof of the status of every lot.
- Selected `search_category` values: 36,34,39,37,38,28,41. Official options
  also include 11 land and 40 agricultural real estate, both unselected.
  The existing seven-category parser scope therefore does not prove complete
  real-estate coverage. Extend only with verified scope/filter regression tests.
- Next: actual page 2/153 reached by site links, individual cards (e.g.
  `https://m-ets.ru/244381-1` and `https://m-ets.ru/244360-1`), expanded categories,
  and source status/initial-current price checks.

## Alfalot

- Tabs 34/35 are the same LotList page 0, UPCOMING/SSALE; tab 36 uses SUPCOMING.
  All show the same single receivable lot `0020626011DS.lot1`, price
  5,024,151.34 RUB, and “Соединение прервано. Пожалуйста, перезагрузите страницу.”
  Do not treat this as successful full property catalogue evidence.
- Official tab links use SUPCOMING / SCURRENT / SPAUSED. Earlier supplied
  UPCOMING differs from the site's own SUPCOMING and must not be assumed equivalent.
- Additional tab 37, `/app#{"filter":{"state":["GD"]},"sort":{"placementDate":false}}`,
  exposes a larger search table: 20 lot links on page 1, visible pages 1/2/3,
  including property lots with price/cadastre/area and non-property assets.
- Navigation exposes `/app/SearchLots/page`. Pagination links are `href="#"`
  with no inline onclick; a verified client request is required before deriving
  an HTTP pagination contract. GD semantics are not proven merely from its code.
- Independent property card observed in links:
  `https://etp.alfalot.ru/app/LotCard/page?LotCard.lotEntity=LT%3Acoreboa1bsm6o0000q38f2filkv08a30`.
- Next: load official search page 2 and last page via controls, two property
  details, and capture sanitized catalogue request/response. Do not keep retrying
  the one-row hall as a property inventory.

## Sberbank-AST

- Tab 38 has the real-estate category selected, but bankruptcy section checkbox
  is unchecked. Visible cards belong to privatization/rent/sale of rights.
  Tab 42 includes movable assets; neither is evidence of a bankruptcy-only scope.
- Tabs 39/41 are SaleRights informational landings; tab 40 is
  SaleBankruptProperty informational landing, not a filtered lot result page.
- Hidden DOM exposes the actual bankruptcy checkbox label/value
  “Продажа имущества (предприятия) банкротов”, plus status, procedure type,
  dates, area, public-offer and other filters.
- Public inline code defines `targetPageCode=UtpESGoodsList`, initial pagenum=0,
  default pagesize=21, and options 6/21/51/99. It serializes a query XML and calls
  `apiRequest('/ESGoodsList','MONITOR','default','','es',...)`.
  It reads `PurchaseList`, `Total`, and BranchGroup aggregation.
- The UI sets `totalRowCount = min(Total,10000)`. Thus a visible last page
  cannot alone prove API completeness beyond 10,000 results. The observed 349
  real-estate pages and 477 unfiltered pages are not bankruptcy last-page proof.
- `filterBody.pagenum` is changed through the site's pagination handler. Actual
  request payload/response must resolve indexing; do not infer it solely from UI.
- Existing code POSTs JSON to `/api/Processing/main`; that transport is still
  code evidence, not newly observed Network evidence from this browser session.
- Next: select bankruptcy in expanded catalogue filters, real estate and
  applicable active states, apply, preserve pages 1/2/last and two exact GoodsView
  links from that result. Do not substitute section-12 privatization cards.

## Remaining acceptance

For all three: full filtered two-pass inventory, independent card consistency,
staging persistence/update/dedupe, restore proof, map and both schedules remain
unproven. Existing older bounded Home results stay historical; these observations
do not silently convert unknown statuses or incomplete paging to PASS.
