# ZakazRF source audit — 2026-11-09

## Scope and safety

Source: `zakazrf.ru` / `https://bankrot.zakazrf.ru/`.

Base main SHA: `34baeaf7202b190e83c1aeaf08ec46f2efa2da76`.

This audit is source-specific and read-only. It does not enable ZakazRF in the
production scheduler, reconciliation, archival, GEO publication or map
publication.

**PRODUCTION ENABLED: NO**

## Previous state

The repository already had a pilot profile for:

- catalogue: `https://bankrot.zakazrf.ru/public/purchases-all/`;
- detail routes:
  - `/public/auctions/lots/view/{lot_id}/`;
  - `/public/contests/lots/view/{lot_id}/`;
  - `/public/public-offers/lots/view/{lot_id}/`;
- ASP.NET postback pagination.

The previous stage evidence was catalogue PASS, 40 candidates, page 2 PASS,
detail PASS and status PASS, while individual price and property
classification were not accepted.

The shared parser derived external identity from `sale_type + lot_id`, but
used generic free-text price/category extraction. That was insufficient for
ZakazRF.

## Root cause

ZakazRF renders the currency in the field label, for example:

`Начальная цена, руб.: 1 125 214 914,36`

The shared ETP money parser expects the amount followed by a RUB marker, so a
valid ZakazRF price could remain null. Broadening the shared parser would
change the contract of unrelated ETPs, therefore this PR adds a ZakazRF-only
money parser.

For public offers the provider exposes a separate `Текущая цена, руб.:`.
It must not be replaced by `Цена на интервале, руб.:`, the deposit or the
auction step.

The catalogue is not property-only. Current official indexed pages show
transport and receivables alongside real estate. ZakazRF detail cards expose
`Классификатор ЕФРСБ`; this is the structured classification evidence used
by this PR instead of free-text procedure prose.

## Fresh 2026-11-09 read-only observations

The official catalogue result observed during this audit showed current
bankruptcy-sale rows, filters for lot/trade number, names, organizer/debtor,
initial-price range, trade type and status, plus ASP.NET-style numbered
pagination. It also showed an active row with application deadline
19.11.2026 and auction date 24.11.2026.

Provider pages observed through current public indexing:

- `/public/auctions/lots/view/18784/`
  - structured classifier: `Автомобили`;
  - property description: transport;
  - proves the source is mixed, not real-estate-only.
- `/public/public-offers/lots/view/18804/`
  - structured property description: real estate;
  - classifier: buildings/structures;
  - public-offer reduction schedule contains interval prices.
- `/public/public-offers/view/821/`
  - lot 1 is a parking space;
  - current price and initial price are separately rendered;
  - status: `Прием заявок`.
- `/public/public-offers/lots/view/18801/`
  - receivables;
  - classifier describes assignment of credit claims;
  - initial price and an interval schedule are present.
- `/public/public-offers/lots/view/18755/`
  - explicit status `Не состоялся`;
  - separate current and initial prices.
- `/public/auctions/lots/view/18785/`
  - individual `Информация о лоте №3`;
  - individual lot number, dates, status and initial price.

The current external audit environment could search current provider pages but
a direct open of the catalogue returned an upstream `UnexpectedStatusCode`.
Therefore this PR does **not** claim that the terminal page or a 30-card direct
live sample was proved from this environment.

## Contract implemented

### Catalogue and identity

The existing dedicated bankruptcy catalogue and detail route patterns are
preserved. Stable IDs remain:

`zakazrf.ru:{sale_type}-{lot_id}`

Examples:

- `zakazrf.ru:auctions-18784`;
- `zakazrf.ru:public-offers-18804`.

No procedure-wide amount is copied to another lot.

### Detail fields

ZakazRF detail parsing now isolates the individual lot section and reads:

- lot number;
- labelled initial price;
- labelled current price, when explicitly present;
- explicit lot status;
- structured EFRSB classifier;
- lot title;
- cadastral number from the lot title;
- area from the lot title;
- address from the lot title;
- application deadline;
- auction date.

### Classification

Only `Классификатор ЕФРСБ` is accepted as structured classification evidence
for ZakazRF. Supported mappings include:

- land;
- real estate;
- vehicle;
- receivables;
- equipment;
- lease/right-of-use.

An unrecognised structured classifier fails closed to `other`; free text is
not allowed to promote it to real estate.

## Regression coverage

Added focused tests for:

1. real-estate public offer with initial/current prices, status, cadastre,
   area, address and structured classifier;
2. vehicle card containing unrelated real-estate boilerplate;
3. receivables classification;
4. refusal to infer initial/current price from deposit or interval-price rows.

## Read-only source probe

`scripts/audit_zakazrf_readonly.py` performs:

- two independent full catalogue passes;
- terminal-page proof;
- ordered external-ID digest comparison;
- duplicate-ID count;
- bounded detail verification (30 by default);
- price/status/classifier success counters;
- category distribution;
- sanitized samples without raw HTML or organizer/debtor personal data.

The probe exits non-zero if both passes do not reach a terminal page or their
ordered IDs differ.

Example:

`python scripts/audit_zakazrf_readonly.py --detail-limit 30 --output zakazrf-audit.json`

## Still not proved in this audit environment

- exact terminal page;
- two complete live catalogue passes and equal digest;
- at least 30 direct live detail requests;
- exact total catalogue size;
- whether every provider status label beyond the observed active/terminal
  examples has been encountered live;
- all possible EFRSB classifier values;
- any catalogue-level structured filter that would safely return only real
  estate. Because the source is mixed, property acceptance remains dependent
  on individual detail classification.

These are intentionally not converted into acceptance claims.

## Files changed

- `src/bankrotai/connectors/etp_sources/source_specific.py`
- `src/bankrotai/connectors/etp_sources/common.py`
- `tests/test_etp_sources.py`
- `scripts/audit_zakazrf_readonly.py`
- `docs/ETP_ZAKAZRF_AUDIT_2026-11-09.md`

No changes to `docs/ROADMAP.md`, `docs/ETP_PARSER_STAGE_CHECKLIST.md`,
production source specs, reconciliation or archival configuration.
