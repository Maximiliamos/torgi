# ETP: first independent Yandex Browser v7 capture, 2026-11-05

**Evidence:** user-supplied private `STERDEZ_ETP_YANDEX_V7_20261105_045226.zip`, 21 public ETPs, captured on Windows in an isolated Yandex Browser without VPN. The raw ZIP contains source HTML (possibly personal information) and must **not** be committed. This is a redacted technical digest.

## Results

- **18/21** public catalogue landing pages returned HTTP **200**. This is **not** 18 successfully operating direct source crawlers.
- **50** saved example detail HTML pages on **17** sources, **607** limited request events, **79** bounded/redacted JSON response previews.
- **12** sources had a distinct set of links on browser navigation to page 2; **NISTP** also reached the real second lot-mode page by direct GET, but v7 reports zero for its generic page-two link metric because it uses a special branch of capture code.
- 3 transport blocks: **RTS-tender HTTP 503**, **Electro-torgi HTTP 429**, **uTender TLS cipher mismatch**. Do not bypass rate limits, TLS, anti-bot gates or CAPTCHA.
- Absolutely **no production DB, geo, map, archive, or lot writes**.

| Source | Landing HTTP | Detail HTML saved | Page 2 new link count* | JSON previews |
|---|---:|---:|---:|---:|
| m-ets.ru | 200 | 3 | 20 | 5 |
| cdtrf.ru | 200 | 0 | 0 | 17 |
| nistp.ru | 200 | 3 | 0* | 0 |
| alfalot.ru | 200 | 2 | 0 | 0 |
| roseltorg.ru | 200 | 3 | 0 | 5 |
| sberbank-ast.ru | 200 | 3 | 0 | 33 |
| fabrikant.ru | 200 | 3 | 0 | 0 |
| arbbitlot.ru | 200 | 3 | 20 | 4 |
| etpu.ru | 200 | 3 | 20 | 3 |
| sistematorg.com | 200 | 3 | 20 | 0 |
| centerr.ru | 200 | 3 | 20 | 4 |
| aukcioncenter.ru | 200 | 3 | 15 | 0 |
| vertrades.ru | 200 | 3 | 100 | 0 |
| rus-on.ru | 200 | 3 | 39 | 0 |
| zakazrf.ru | 200 | 3 | 20 | 4 |
| utpl.ru | 200 | 3 | 20 | 4 |
| rts-tender.ru | 503 | 0 | 0 | 0 |
| etp-profit.ru | 200 | 3 | 8 | 0 |
| ausib.ru | 200 | 3 | 15 | 0 |
| utender.ru | TLS | 0 | 0 | 0 |
| electro-torgi.ru | 429 | 0 | 0 | 0 |

*Page-2 new-link count is only the v7 collector's observed set difference, not certified lot identity or a complete source scan; NISTP has independent network and HTML proof of page 2 despite its generic metric being 0.*

## Provider contracts and precise findings

1. **NISTP (P1).** The actual per-lot register is `GET https://nistp.ru/?...&view_type=lot&pagenum=2`, not the default trade catalogue `/bankrot/trade_list.php`. Saved page-2 HTML shows **seven-column individual lot rows**: trade code, organizer, debtor + numbered lot, start price, opening date, closing date, source status. Trade `trade_nid=502957` has lot 4 and lot 5 at the same confirmed initial price RUB 3,274,000, but **different cadastre and area**. NISTP form `#nodesearch` uses GET with `action=""`, checked `view_type=lot` radio, and hidden `pagenum`. Blindly appending new `params` to an action URL containing old query duplicates filters. [#1093](https://github.com/Maximiliamos/torgi/pull/1093) changes the default listing to individual lots, parses actual per-row money/status/cadastre and fixes GET query replacement. [#1100](https://github.com/Maximiliamos/torgi/pull/1100) validates distinct detail tables under one trade URL.
2. **CDTRF (P1).** The landing Nuxt page returns 200 but the 17 captured JSON previews are principally `/api/Catalog/tradeForm`, `tradeKind`, `LotCommonCategory`, `Region` and CMS metadata; no actual lot cards were captured. This **does not prove** the list/detail endpoint is working. Need navigate to the provider's public trade list and capture its actual catalogue data request.
3. **Profit (P1 transport).** Browser successfully loaded `/lots`, `/lots?page=2` and three `/(oaof|otpp)/.../lots/...` pages over HTTPS. Home Python transport previously hit TLS certificate validation failure. Browser success does not certify trust configuration for requests; **do not use `verify=False`**.
4. **AUSIB (P1 transport).** Browser saw `302 -> 200` on the public catalogue and then successfully loaded `?page=2` and three `/trade/view/purchase/general.html?id=...` pages. Distinguish legitimate same-host browser redirects from current strict no-redirect server policy; do not silently permit cross-host redirects.
5. **Roseltorg (P1 transport + identity).** Browser reached `/search/sale` and three `/procedure/{procedure-code}/1` pages, with public Drupal AJAX `POST` requests. Provider code may contain alphabetic characters (`COM...`) as well as digits. Browser HTTP 200 does not resolve earlier Home connection errors or prove every procedure maps to one sellable asset.
6. **Fabrikant (P1 catalogue).** HTML contains only 9 candidate links to `/v2/trades/procedure/view/{id}`, with Next.js `text/x-component` GET/POST traffic. They are *procedure links*, not independently proven lot IDs. V7 did not verify page 2.
7. **Sberbank-AST (P1 catalogue).** Document `/ESGoodsList.html` and POST `/api/Processing/main` are publicly visible; 33 JSON previews captured and three goods detail pages loaded on `utp.sberbank-ast.ru`. Need independently verify bankruptcy source-term filter, stable individual goods identity and pagination `Total`, not infer from generic GUI.
8. **ASP.NET group (P1).** Arbbitlot, ETPU, Centerr, ZakazRF and UTPL successfully switched to a different page via `__doPostBack` controls; lot-specific price and status still need independent detail comparison.
9. **Legacy GET/form group (P1).** M-ETS, Aukcioncenter, Vertrades, Profit, AUSIB, Sistematorg and Rus-on have distinct observed page-2 links. A valid second page **is not** proof of terminal-page coverage.
10. **v7 collector (P2).** At Vertrades, the collector emitted **51 detail_http_error** events for URL variants while only three HTML detail pages were saved. Deduplicate navigation by canonical first-party path plus approved identity query keys and cap failed attempts. For Aukcioncenter/AUSIB, network logging hit its 180-entry ceiling, so a lack of API entries after that point is not proof of absence.

## Required acceptance gates

Do not call any source accepted until: source-specific first/last pagination, exact individual lot IDs, independent detail price/status/date/cadastral crosschecks, property-only selection, duplicate prevention, two comparable complete crawls, backup+restore, staging import, PostgreSQL journal/GEO/map verification, and stable scheduled runs. New sources must stay non-archiving until proven complete.

No network cookies, request bodies, personal names, emails, raw HTML or original ZIP are committed with this report.
