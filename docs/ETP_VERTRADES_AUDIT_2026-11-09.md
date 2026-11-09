# Vertrades source-contract audit — 2026-11-09

**Assigned source:** `vertrades.ru`, official arrested-property ETP on `arest.vertrades.ru`.

**Exact work base SHA:** `34baeaf7202b190e83c1aeaf08ec46f2efa2da76`.

**PRODUCTION ENABLED: NO.** No scheduler, allowlist, reconciliation, archival or source-maturity change is part of this work.

## Previous state and root cause

Reported pre-audit state: catalogue PASS, 40 candidates, page 2 PASS, individual detail BLOCKED, price FAIL, status FAIL.

The profile already pointed at:

- catalogue: `https://arest.vertrades.ru/bidding`;
- shared procedure card: `https://arest.vertrades.ru/showTrades?id={provider_procedure_id}`;
- GET pagination parameter: `page`.

The detail failure was an intentional code guard, not an authentication failure. `HtmlEtpConnector.fetch_lot()` unconditionally raised `SourceAccessError` for `vertrades.ru` because a single `showTrades?id=...` card can contain many independently priced lots and no proven section isolation existed.

A second defect was found during this audit: the direct catalogue extractor accepted only numeric trade identifiers, while the public register renders identities such as `150-АП`, `149-АЗ`, `108-АП` and `27-АП`.

## Official public contract rechecked read-only

### Catalogue and filters

The official register remains `https://arest.vertrades.ru/bidding`. The public page exposes the trade/lot table, initial-price column, dates and status plus search fields for trade number, lot name, organizer/debtor, minimum initial price, date range and status.

Observed first-party GET routes:

- all trades: `/bidding`;
- open auctions: `/bidding?bidding_type=1`;
- closed tenders: `/bidding?bidding_type=2`;
- pagination: `/bidding?page=N`.

No separate first-party XHR/JSON API was proved or required for the parser. The source-specific live check now inspects the public search form structurally on page 1 and records only its HTTP method, same-origin action path and field names (never field values). The same form contract must match on both catalogue passes. This makes the GET/POST transport evidence explicit without collecting query contents or personal data.

### Pagination

Read-only page retrieval proved:

- page 1 starts with procedure `150-АП`;
- page 2 starts with `108-АП`;
- page 3 starts with `69-АП`;
- page 4 starts with `27-АП`;
- page 4 paginator exposes `1 … 3 4` and no page 5 control.

Therefore **terminal page = 4 is proved for the currently published all-trades register**.

The browser retrieval layer was repeated and the page-leading procedure identities remained `150-АП / 108-АП / 69-АП / 27-АП`. The local agent container has no outbound DNS, so this branch adds a bounded source-specific GitHub Actions proof: exactly two passes over at most four public catalogue pages (maximum 8 GETs), comparing all parsed composite IDs and their SHA-256 digest. It stores no raw HTML or personal data.

### Mixed catalogue and property scope

The register is not real-estate-only. Public pages mix:

- apartments, rooms, buildings and land;
- cars/buses;
- equipment;
- receivables and other assets.

The parser therefore keeps the existing positive property-classification gate and now has a Vertrades regression fixture proving that vehicle, equipment and receivable rows are excluded while real estate/land rows survive. No procedure type or status is used as a substitute for property classification.

### Procedure versus individual lot identity

The catalogue shows a visible procedure code and an explicit lot number. Multiple lot links of one procedure resolve to the same shared first-party route:

`https://arest.vertrades.ru/showTrades?id=<provider_procedure_id>`

The `id` query value identifies the shared provider procedure card, not an individual lot.

The verified parser identity is therefore:

`vertrades.ru:<provider_procedure_id>:<lot_number>`

The visible procedure code (for example `150-АП`) remains independently recorded and is cross-checked between catalogue and detail. Unicode dash variants are canonicalized to `-`.

## Individual detail contract

Public shared card used for the primary 30-lot audit:

- provider procedure ID: `102288306`;
- visible procedure: `150-АП`;
- detail: `https://arest.vertrades.ru/showTrades?id=102288306`.

The server-rendered card contains explicit sections headed `Сведения по лоту №N`. Inside each section the provider labels:

- `Статус торгов по лоту`;
- `Краткие сведения об имуществе (наименование лота)`;
- detailed asset description;
- `Начальная цена продажи имущества`;
- bid increment;
- `Размер задатка`.

Procedure-level dates are explicitly labelled at the top of the same card.

### 30 individual lot sections checked

The following 30 distinct numbered sections were checked on the public card:

`1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 24, 25, 26, 27, 28, 29, 33, 34, 35`.

Result:

- unique requested lot identities: **30/30**;
- explicit per-lot initial price: **30/30**;
- explicit per-lot status: **30/30**;
- both completed and cancelled per-lot statuses are present;
- no price is taken from the deposit, bid step, procedure text or neighboring section.

Additional sections later in the same card (36, 38–48, 50, etc.) confirm the same shared-card structure. A second public procedure card (`102259678`, visible procedure `55-АП`) was also inspected and shows the same numbered section/price/status contract, including cancelled lots.

The source-specific CI additionally fetches the shared `102288306` card once from the provider and applies the exact-lot parser to all 30 selected lot numbers from that single response. The check requires 30/30 explicit prices and 30/30 recognized per-lot statuses and emits only aggregate counts plus SHA-256, never source HTML, names, addresses or descriptions.

The public catalogue currently served by the site is historical (primarily 2021–2022 records); no claim is made that these auctions are active in 2026. They are, however, the records currently published by the official register and were rechecked on 2026-11-09.

## Fields and fail-closed rules

The new source-specific detail parser extracts only from the exact requested lot section:

- lot number;
- title and asset description;
- explicitly labelled initial price;
- explicitly labelled per-lot status;
- optional deposit;
- address when explicitly present;
- cadastral number when matched in the requested asset text;
- first explicit square-metre area;
- procedure-level application start/end and price-offer start;
- visible procedure code.

Fail closed when:

- external ID syntax is invalid;
- provider procedure ID is invalid;
- requested lot section is missing or duplicated;
- procedure number is missing/ambiguous;
- required title, price or status is missing/ambiguous;
- status text is not one of the proven forms;
- catalogue and detail procedure/lot/cadastre/initial-price evidence conflicts;
- provider redirects/refuses the public GET.

Known detail status forms explicitly covered include `Торги завершены`, `Торги по лоту отменены` and catalogue wording `Завершенные торги`.

## Changed code

- `src/bankrotai/connectors/etp_sources/vertrades_detail_sections.py` — exact requested-lot section parser.
- `src/bankrotai/connectors/etp_sources/vertrades_detail_adapter.py` — public `showTrades?id=...` fetch/enrichment adapter with identity and price cross-checks.
- `src/bankrotai/connectors/etp_sources/common.py` — source-specific adapter hook and source-specific status normalization only for Vertrades.
- `src/bankrotai/connectors/registry/direct_etp_catalogue.py` — parse real `NNN-АП/NNN-АЗ` procedure codes; existing positive property gate remains.
- `src/bankrotai/services/etp_vertrades_contract_probe.py` — bounded two-pass pagination/ID-digest probe, safe filter-form transport inspection and one-response 30-lot live detail proof; executable as `python -m bankrotai.services.etp_vertrades_contract_probe`.
- `tests/test_direct_etp_catalogue_batch.py` — the old synthetic numeric-only Vertrades trade ID was replaced with the verified `NNN-АП` format; assertions remain strict.
- `tests/test_vertrades_catalogue_contract.py` — mixed-asset/property and composite-ID regression.
- `tests/test_vertrades_detail_sections.py` — section isolation, price/status, dates, address/cadastre/area and fail-closed cases.
- `tests/test_vertrades_detail_adapter.py` — fetch/enrichment identity, conflict and redirect regressions.
- `tests/test_etp_vertrades_contract_probe.py` — two-pass/terminal/digest/safety regressions plus the 30-lot detail-sample contract.
- `.github/workflows/etp-vertrades-contract-readonly.yml` — source-specific PR check: focused Vertrades tests, then live two-pass catalogue proof and 30-lot detail proof.
- this audit document.

No neighboring ETP parser, `docs/ROADMAP.md`, centralized stage checklist, production scheduling, reconciliation or archival logic was changed.

## Verification matrix

| Check | Result |
| --- | --- |
| Official catalogue | PASS — `arest.vertrades.ru/bidding` |
| Correct source section | PASS — arrested-property subdomain only; bankruptcy subdomain excluded |
| Page 1 | PASS |
| Page 2 | PASS |
| Page 3 | PASS |
| Terminal page | PASS — page 4 |
| Mixed asset classification | PASS — real estate separated from vehicles/equipment/receivables by positive property evidence |
| Stable lot identity model | PASS — provider procedure ID + explicit lot number |
| Public detail route | PASS — server-rendered GET `showTrades?id=...` |
| 30 individual detail sections | PASS — 30 distinct lot numbers |
| Per-lot initial price | 30/30 explicit |
| Per-lot status | 30/30 explicit |
| Cancelled status | PASS |
| Dates | PASS at procedure level |
| Title/address/cadastre/area | Proven where explicitly present in requested section; optional fields remain null when absent |
| Two browser-layer catalogue passes | PASS for page/leading-procedure stability |
| Full raw-ID SHA-256 two-pass run | Source-specific network CI check included; exact run output is recorded in PR checks |
| CAPTCHA/auth bypass | None attempted |
| Production enablement | NO |

## Remaining limitations

1. The source's public register is stale/historical; no active 2026 auction sample was observed.
2. The live probe records the public search form's method/action/field names but intentionally does not submit arbitrary user search values; the canonical unfiltered catalogue is sufficient for source inventory proof.
3. The exact two-pass SHA-256 result from `etp_vertrades_contract_probe.py` still requires a network-capable repository runtime; the current agent container cannot resolve external DNS.
4. Source-specific focused tests and live proof plus the repository-wide CI must be green before this PR is treated as review-ready.

## Acceptance boundary

This PR establishes and implements a fail-closed Vertrades source contract. It does **not** constitute production acceptance and does not authorize reconciliation or archival.

**PRODUCTION ENABLED: NO**
