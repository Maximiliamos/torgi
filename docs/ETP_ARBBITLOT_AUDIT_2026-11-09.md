# ARBbitLot source contract audit — 2026-11-09

## Scope

Source: `arbbitlot.ru` / АРБбитЛот.

Base SHA before source-specific changes:

`34baeaf7202b190e83c1aeaf08ec46f2efa2da76`

This audit is read-only. It does not enable scheduling, reconciliation, archival, or production ingestion.

**PRODUCTION ENABLED: NO**

## Previous state

Observed before this change:

- catalogue: PASS;
- approximately 40 candidates in the previous bounded probe;
- page 2: PASS;
- individual detail: PASS;
- status: PASS;
- individual detail price: FAIL;
- terminal page: not proven.

The existing profile already used the official catalogue:

`https://torgi.arbbitlot.ru/public/purchases-all/`

and individual lot URLs:

`/public/{auctions|contests|public-offers}/lots/view/{lot_id}/`.

The existing ASP.NET pagination implementation preserves hidden form fields and submits the provider's own `__doPostBack` event target.

## Root cause

ARBbitLot renders its individual money fields with the currency in the label before the number, for example:

- `Начальная цена, руб.: 452 906,10`;
- `Текущая цена, руб.: 169 470,00`;
- `Шаг, руб.: 500 000,00`.

The generic ETP money expression expects the amount before the RUB suffix. As a result, a valid ARBbitLot individual card could have a visible initial price while `parse_detail_html()` returned `start_price=None`.

Using the neighbouring amount as a fallback would be unsafe because the same card can contain a deposit, auction step, public-offer interval price and other monetary values.

## Official contract proven from the public site

### Catalogue and sections

Official public catalogue:

`https://torgi.arbbitlot.ru/public/purchases-all/`

The public interface exposes separate sections for auctions, contests, public offers and all trades. The catalogue has explicit columns for trade number, lot number, lot, initial price, organizer, dates and status.

Public offers additionally expose separate columns for **initial price** and **current price**.

The catalogue is mixed. Current/recent official rows include real estate and land as well as vehicles, receivables, weapons/equipment and other property. A source-wide assumption that every ARBbitLot row is real estate is therefore invalid.

### Identity

Individual detail URL identity is the provider's per-lot URL identity:

- `arbbitlot.ru:auctions-<lot_id>`;
- `arbbitlot.ru:contests-<lot_id>`;
- `arbbitlot.ru:public-offers-<lot_id>`.

The procedure/trade number and the displayed lot number are separate fields. A procedure may contain multiple lots. Live/recent official evidence includes procedures with two or more rows whose lot numbers, descriptions and prices differ. The parser must therefore never use the procedure number alone as the sellable-lot identity.

The catalogue adapter now records all three relevant identities:

- provider procedure/trade number;
- displayed lot number within that procedure;
- individual lot URL identity used in `external_id`.

During enrichment, catalogue procedure number and displayed lot number are compared against the individual detail card. A mismatch is fail-closed rather than silently attaching another lot's price or status.

### Individual detail

Official individual detail examples observed in the 2026-11-09 read-only audit included these provider lot IDs:

`45280, 45309, 45093, 44026, 45288, 40663, 45337, 41617, 41822, 44923, 42329, 42098, 42150, 42147, 45324, 45251, 41395, 43424, 42011, 43118, 45134, 41009, 42077, 43511, 41376, 42482, 42886, 42897, 40905, 40773, 40774, 41490, 36532, 43227, 45298, 44735, 34978, 42444, 40783, 42108, 41489`.

This is more than 30 distinct official individual-card identities across the current/recent 2026 public templates. Search-index availability is not treated as a substitute for a complete direct catalogue pass; the new read-only probe performs the direct verification.

Observed explicit price examples include:

- public offer `40663`: initial `242100.00`, current `169470.00`;
- public offer `41617`: initial `1000000.00`, current `800000.00`;
- public offer `42329`: initial `824999.40`, current `527999.62`;
- public offer `42482`: initial `549000.00`, current `109800.00`;
- auction `41009`: initial `171004.50`, explicit percent step;
- auction `41376`: initial `3674000.00`, explicit percent step.

These examples prove that current public-offer price is independent data and must not be synthesized by copying the initial price.

### Deposit and auction step

ARBbitLot exposes deposit/security and step data independently from the initial/current price. The source-specific parser accepts monetary values only when the source supplies an explicit RUB-labelled field. Percent step is parsed independently from RUB step.

This prevents:

- a deposit from becoming the lot price;
- `Шаг, % ... 5,00` from becoming a five-ruble step;
- a public-offer interval amount from silently replacing the initial price;
- an adjacent monetary value from satisfying a missing price field.

### Public-offer current price and interval tables

The official **public-offers catalogue** has distinct `Начальная цена, руб.` and `Текущая цена, руб.` columns on the same individual lot row. That row is already tied to the provider's individual `/public/public-offers/lots/view/<lot_id>/` URL, so the catalogue current price is lot-level evidence rather than a procedure-level fallback.

Official public-offer lot cards can also contain an interval table with columns such as interval dates, reduction, interval deposit and `Цена на интервале, руб.`. The detail parser deliberately does not infer a current price from an arbitrary nearby interval row. If the detail has its own explicit current-price label it is used; otherwise the independently labelled catalogue current price is preserved and marked as catalogue-derived evidence.

This avoids synthesizing current price from the clock, a stale interval, the deposit column, or the reduction amount.

## Pagination

The provider uses ASP.NET postback pagination. The public pager groups numeric links and visibly exposes pages 1 through 10 plus `>>`.

A source-specific defect was found during this audit: the previous generic postback code looked only for a literal `page + 1` link. On page 10 the provider does not expose a visible `11` link; it exposes `>>`. Therefore page 10 could be misclassified as terminal.

ARBbitLot behavior is now:

1. GET page 1;
2. preserve hidden fields such as `__VIEWSTATE`;
3. use the exact numeric `__doPostBack(...)` target while `page + 1` is visible;
4. at a pager-group boundary, use the provider's own `>>` postback target;
5. continue with the newly exposed numeric page group;
6. reject repeated page fingerprints;
7. declare terminal only when neither the next numeric page nor ARBbitLot's `>>` control exists.

A direct terminal-page run still cannot be truthfully claimed from search-index evidence alone; it must come from the read-only live probe.

A new read-only audit module was added:

`python -m bankrotai.services.etp_arbbitlot_readonly_audit --detail-limit 30`

It performs two independent complete catalogue passes, stops only at the provider-derived terminal condition, compares unique IDs/digests, reports duplicate IDs and verifies up to 30 individual details. It does not write to the database.

Until that direct probe completes in a network environment that can submit the provider's ASP.NET form, **terminal page remains a required live acceptance item** rather than a guessed PASS.

## Additional live status evidence

The current official catalogue includes the status phrase `Отменён организатором`. The generic status normalizer did not classify that exact source wording as closed and could later see unrelated `Извещение` text on a detail page.

ARBbitLot now handles this provider-specific phrase as `closed` without changing status behavior for neighbouring ETPs. Regression coverage includes both catalogue and individual-detail forms.

## Code changes

### `src/bankrotai/connectors/etp_sources/arbbitlot.py`

All ARBbitLot-specific parsing and validation now lives in one isolated module:

- official catalogue profile/constants;
- catalogue row parsing and displayed procedure/lot identity;
- individual detail parsing;
- initial price;
- current price;
- deposit/security;
- auction step in RUB and percent;
- structured EFRSB property classifier;
- source-specific cancelled status;
- catalogue/detail identity checks;
- catalogue/detail price checks;
- ASP.NET numeric pagination;
- grouped `>>` pagination;
- terminal-page decision;
- postback request construction;
- standalone `ArbbitlotConnector`;
- source-specific fail-closed errors.

The module reuses generic ETP primitives only where their semantics are source-independent. No ARBbitLot branches remain in the shared parser.

### `src/bankrotai/services/etp_arbbitlot_readonly_audit.py`

The audit imports `ArbbitlotConnector` directly from the isolated module. It does not depend on registration in the shared dispatcher.

### `tests/test_arbbitlot_contract.py`

Regression tests exercise the isolated module directly, including the original price root cause, multi-lot identity, current-price separation, cancelled status, classifier handling and grouped ASP.NET pagination.

### `tests/test_etp_arbbitlot_readonly_audit.py`

Covers the two-pass audit report, terminal proof, duplicate detection, per-lot identity comparison and price/status counters.

### `.github/workflows/etp-arbbitlot-readonly-audit.yml`

Source-specific workflow runs exact-head focused tests and the live read-only two-pass/30-detail audit. It does not mutate the database or production configuration.

## Integration hook required

The isolated module is intentionally not registered from this source PR.

A future integration PR only needs to wire ARBbitLot's dispatcher entry to:

`bankrotai.connectors.etp_sources.arbbitlot.ArbbitlotConnector`

The appropriate integration point is the shared ETP connector registration in
`src/bankrotai/connectors/etp_sources/__init__.py` (or its future centralized replacement).

No ARBbitLot-specific parsing, price, status, identity or pagination behavior needs to be added to `common.py`, `pagination.py`, `profiles.py`, `source_specific.py` or `direct_etp_catalogue.py`.

## Isolation review

Source-specific module:

`src/bankrotai/connectors/etp_sources/arbbitlot.py`

Shared files modified:

NONE

Source-specific logic remaining in `common.py`:

NONE

Source-specific logic remaining in `pagination.py`:

NONE

Source-specific logic remaining in `profiles.py`:

NONE

Source-specific logic remaining in `source_specific.py`:

NONE

Source-specific logic remaining in `direct_etp_catalogue.py`:

NONE

Integration hook required:

Register `ArbbitlotConnector` for source ID `arbbitlot.ru` in the centralized integration-owned dispatcher.

Existing work preserved:

YES

Live evidence preserved:

YES

Regression tests preserved:

YES

PRODUCTION ENABLED:

NO

## Not changed

This PR does not change:

- any neighbouring ETP parser;
- shared ETP parser behavior;
- production scheduler;
- source allowlists;
- reconciliation;
- archival;
- `docs/ROADMAP.md`;
- `docs/ETP_PARSER_STAGE_CHECKLIST.md`.

## Remaining limitations / acceptance items

1. Execute the new direct read-only probe against the live provider and retain its JSON result.
2. Require at least 30 successful direct individual GETs if the live catalogue exposes at least 30 cards at run time.
3. Record the actual terminal page and two-pass ID digests.
4. If two passes differ because the source changes during the audit, compare the exact additions/removals instead of declaring pagination broken.
5. Full public-offer interval schedule normalization is not added by this PR; only explicit per-lot start/current price is trusted.
6. Production acceptance remains a separate review decision.

**PRODUCTION ENABLED: NO**
