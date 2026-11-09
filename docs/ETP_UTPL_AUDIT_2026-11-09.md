# UTPL / Объединённая торговая площадка — source audit, 2026-11-09

**Scope:** `utpl.ru` only.  
**Base SHA:** `5997d239a71e253b7dd3386820ca08002f63f071`.  
**PRODUCTION ENABLED: NO.**

No scheduler, allowlist, ingestion, reconciliation, archival, GEO, map publication,
`docs/ROADMAP.md`, or shared ETP checklist changes are part of this work.

## Previous state and root cause

Operator baseline supplied for this assignment:

- catalogue PASS, 40 candidates;
- page 2 PASS;
- individual detail PASS;
- status PASS;
- price FAIL;
- property classification FAIL.

Existing UTPL profile correctly pointed at
`https://bankrupt.utpl.ru/public/purchases-all/`, but the generic detail
parser treated the entire procedure document as one lot. That was unsafe because
a UTPL procedure may contain several independently priced lots. Procedure text,
deposit values, public-offer interval prices and another asset description could
therefore contaminate a selected lot. Generic category matching also could not
preserve mixed structured types such as real estate + land + lease + equipment.

A second defect was found in pagination: the generic ASP.NET helper expected a
visible numeric `page + 1` link. Current UTPL pagers expose blocks
`1 2 ... 10 >>`; at a block boundary this could produce a false terminal page.

## Official catalogue contract observed

Official public catalogues retrieved during the audit:

- all trades: `https://bankrupt.utpl.ru/public/purchases-all/`;
- auctions: `https://bankrupt.utpl.ru/public/auctions-all/`;
- public offers: `https://bankrupt.utpl.ru/public/public-offers-all/`.

The catalogue is **lot-level**, not procedure-only. Rows separately expose
procedure number, `№ лота`, lot title, initial price, dates and status.
Public-offer rows additionally expose a separate current price.

Current multi-lot examples prove that procedure values cannot be copied between
lots:

| procedure | lots observed | proof |
| --- | --- | --- |
| `0009410` | 1, 2 | distinct rows; initial prices RUB 33,836,400 and 24,444,000 |
| `0009382` | 1, 2, 3, 4 | four individual equipment lots with different initial prices |
| `0009399` | 1, 2 | distinct receivable lots with different initial prices |
| `0009298` | 1, 2 | separate real-estate assets and prices |
| public offer `0009365` | 1, 2, 3 | separate receivable lots with individual current/initial prices |

The operational external identity remains the provider's canonical detail
identity, e.g. `utpl.ru:auctions-128933` or
`utpl.ru:public-offers-127755`. Detail parsing now also retains the separate
UTPL procedure number and the lot number from `Информация о лоте №N`.

## Individual detail contract

Only the numbered individual section may supply lot fields:

- `Информация о лоте №N`;
- `Номер:`;
- `Наименование:`;
- `Статус:`;
- `Начальная цена, руб.:`;
- `Текущая цена, руб.:` for public offers;
- application/auction dates;
- `Сведения об имуществе должника...`;
- `Классификатор ЕФРСБ:`.

Deposit, auction step and public-offer interval-table amounts are not accepted as
the lot's initial/current summary price.

Explicit UTPL statuses observed and mapped without date inference:
`Прием заявок`, `Извещение опубликовано`, `Не состоялся`, `Окончен`,
`Отменён организатором`, and `Приостановлен` when explicitly present.
Unknown labels remain `unknown`.

## Detail evidence sample

During this audit, **41 distinct official individual detail URLs** were inspected
through provider-page retrieval. Detail IDs:

`128933, 128941, 128964, 127873, 128936, 127518, 127755, 127880, 127683,
127513, 127686, 127730, 127862, 127419, 127869, 127610, 127661, 127606,
126754, 125909, 126500, 127830, 127664, 127832, 126917, 126728, 126553,
126782, 127677, 127676, 127448, 126719, 126017, 126552, 113828, 113829,
127345, 117690, 125859, 124600, 127525`.

This exceeds the requested 30-card manual sample. It is **not** a substitute for
the automated two-pass catalogue walk described below.

Representative directly surfaced detail pairs:

| detail ID | kind | status | initial/current evidence |
| --- | --- | --- | --- |
| `128933` | vehicle | Извещение опубликовано | initial RUB 1,531,000 |
| `128964` | receivable description | Извещение опубликовано | initial RUB 9,904,782.72 |
| `127873` | equipment | Прием заявок | initial RUB 340,000 |
| `127518` | auction | Не состоялся | initial RUB 695,066.75 |
| `127755` | public offer | Окончен | initial RUB 22,194,000; current RUB 13,316,400 |
| `127683` | vehicle | Не состоялся | initial RUB 108,000 |
| `127513` | receivable public offer | Прием заявок | initial RUB 264,055; current RUB 10,007.68 |
| `127686` | auction | Не состоялся | initial RUB 11,005,314.13 |
| `127730` | land | Прием заявок | initial RUB 81,626,624 |
| `126754` | public offer | Окончен | initial RUB 1,329,642.08; current RUB 132,964.21 |
| `125909` | equipment public offer | Окончен | initial RUB 1,387,800; current RUB 416,340 |
| `126500` | land | provider lot status/initial price separately labelled | classifier = land |
| `127525` | auction | cancelled/terminal provider state observed | individual detail |
| `127527` | auction | Отменён организатором | initial RUB 2,708,333.33 |

For the manually surfaced subset with both fields visible, price and status are
bound to the same numbered detail section. The automated 30-card success rate is
reported separately and must not be inferred from this table.

## Structured property classification evidence

UTPL publishes `Классификатор ЕФРСБ`; it is the primary property-kind
evidence. Observed classes include:

- `Земельные участки`;
- `Жилые здания (помещения)` + `Земельные участки` on the same lot;
- industrial buildings/structures;
- automobiles;
- production / industrial equipment;
- specialized equipment;
- `Уступка требований по кредитным обязательствам`;
- trademarks / intellectual property.

A mixed official detail (`127661`) contains a land parcel, building,
structure, **lease right** to another parcel and equipment in one individual
lot. The parser therefore retains `utpl_property_kinds` instead of collapsing
all meaning into a single free-text category. Rights/lease are never converted
to ordinary land ownership merely because a parcel or cadastral number is
mentioned.

If the structured classifier is absent, an explicit lot-scoped description such
as `Право требования` may still safely classify the lot as receivables. The
procedure header is never used for this fallback.

## Pagination contract and source-end proof

UTPL uses ASP.NET postbacks with hidden form state. The parser now:

1. accepts an explicit provider `__doPostBack` for the numeric next page;
2. at a ten-page block boundary accepts only the provider-supplied forward
   control (`>>` / equivalent);
3. never synthesizes an event target;
4. treats absence of both controls as terminal;
5. rejects repeated catalogue documents and duplicate lot IDs in the audit
   probe.

Historical redacted repository evidence from 2026-10-29 records UTPL page 2 as
distinct, but did not prove the source end.

`scripts/probe_utpl_readonly.py --catalogue --passes 2 --details 30` now
performs the missing bounded proof: full page walk to source end, unique ID set,
per-pass digest, two-pass comparison and 30 individual detail parses. It is
read-only and explicitly leaves `complete_source_inventory=false`,
`safe_for_archival=false`, `production_enabled=false`.

**Fresh automated terminal/two-pass result:** pending the source-specific
network-enabled PR audit run. Do not mark the source complete from manual/indexed
evidence alone.

## Code changes

- `src/bankrotai/connectors/etp_sources/utpl_contract.py`
  - individual section isolation;
  - initial/current price binding;
  - exact UTPL status normalization;
  - structured classifier/property kinds;
  - lot-scoped cadastral/area/VIN/address/dates;
  - separate procedure + lot number;
  - provider-only next-postback parsing.
- `src/bankrotai/connectors/etp_sources/common.py`
  - UTPL identity guard;
  - UTPL detail enrichment hook;
  - UTPL lot-scoped fields/raw evidence;
  - provider-supplied postback use.
- `src/bankrotai/connectors/etp_sources/pagination.py`
  - UTPL block-boundary terminal detection.
- `tests/test_utpl_lot_contract.py`
  - sanitized regressions for prices, status, rights, lease, mixed
    classification, identity, page-block boundary and terminal behavior.
- `scripts/probe_utpl_readonly.py`
  - bounded two-pass full catalogue and detail audit.
- `docs/ETP_UTPL_AUDIT_2026-11-09.md`.

## Quantitative gates

| Gate | Current result |
| --- | --- |
| Official catalogue route | **PROVEN** |
| Catalogue rows are lot-level | **PROVEN** |
| Multi-lot per-lot price/identity | **PROVEN on multiple procedures** |
| Distinct manual individual detail URLs inspected | **41** |
| Structured property-kind distinctions | **PROVEN on representative land/real-estate/vehicle/equipment/receivable/lease/IP cases** |
| Fresh page 1/page 2 via automated probe | **PENDING RUN** |
| Fresh terminal page via automated probe | **PENDING RUN** |
| Two full passes / ID digest | **PENDING RUN** |
| Automated 30-detail price success | **PENDING RUN** |
| Automated 30-detail status success | **PENDING RUN** |
| Focused tests on current PR head | **PENDING CI** |
| Full applicable CI | **PENDING CI** |
| Safe for reconciliation / archival | **NO** |
| Production accepted | **NO** |

**PRODUCTION ENABLED: NO**
