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

### Public-offer interval tables

Official public-offer lot cards can also contain an interval table with columns such as interval dates, reduction, interval deposit and interval price. That is useful evidence for public-offer schedules, but this PR does not infer a full schedule into production fields merely from nearby text. The verified first-class value in this patch is the explicitly labelled current price on the individual lot card.

## Pagination

The provider uses ASP.NET postback pagination. Existing source-specific connector behavior:

1. GET page 1;
2. preserve hidden fields such as `__VIEWSTATE`;
3. locate the provider's page-number anchor containing `__doPostBack(...)`;
4. POST the exact event target/argument;
5. reject repeated page fingerprints;
6. set `next_cursor=None` only when the returned page no longer exposes a next page.

The public catalogue visibly exposes pages 1 through 10 plus `>>`. A direct terminal-page run cannot be truthfully claimed from search-index evidence alone.

A new read-only audit module was added:

`python -m bankrotai.services.etp_arbbitlot_readonly_audit --detail-limit 30`

It performs two independent complete catalogue passes, stops only at the provider-derived terminal condition, compares unique IDs/digests, reports duplicate IDs and verifies up to 30 individual details. It does not write to the database.

Until that direct probe completes in a network environment that can submit the provider's ASP.NET form, **terminal page remains a required live acceptance item** rather than a guessed PASS.

## Code changes

### `src/bankrotai/connectors/etp_sources/source_specific.py`

Added an ARBbitLot-specific labelled-field parser for:

- initial price;
- current price;
- deposit/security amount;
- deposit percent;
- auction step amount;
- auction step percent;
- trade/procedure number;
- displayed lot number.

ARBbitLot detail enrichment now fails closed with
`arbbitlot_detail_start_price_unverified`
when the individual detail does not prove an initial price. A catalogue price is not promoted to verified detail price in that case.

### `src/bankrotai/connectors/etp_sources/common.py`

ARBbitLot detail parsing now consumes the source-specific fields and keeps initial/current/deposit/step separate. It also records procedure ID and displayed lot number in detail evidence and extracts address where the source provides the labelled field.

### `src/bankrotai/services/etp_arbbitlot_readonly_audit.py`

Added a source-specific non-persistent two-pass catalogue/detail audit.

### Tests

Added regression coverage for:

- ARBbitLot currency-before-number price format;
- initial/current/deposit/step isolation;
- missing start price not inferred from deposit or step;
- fail-closed detail enrichment;
- stable distinct identities for a multi-lot procedure;
- ASP.NET page-2 postback and terminal condition;
- two-pass read-only audit digest and detail statistics.

## Not changed

This PR does not change:

- any other ETP profile or parser contract;
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
