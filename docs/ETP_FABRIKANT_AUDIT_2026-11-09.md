# Fabrikant source audit — 2026-11-09

## Scope

Source: `fabrikant.ru` only.

Exact working base SHA captured before any change:

`34baeaf7202b190e83c1aeaf08ec46f2efa2da76`

Branch:

`agent9/fabrikant-audit-20261109`

Production enablement is explicitly out of scope.

**PRODUCTION ENABLED: NO**

## Previous state

The parser-stage evidence before this audit was:

- catalogue: PASS;
- only 9 observed candidates;
- page 2 / terminal page: NOT PROVEN;
- detail reachability: PASS;
- individual price: FAIL;
- individual status: FAIL.

The old profile matched:

`/v2/trades/procedure/view/{token}`

and treated that single procedure token as a lot identity.

That assumption was unsafe.

## Root cause

Fabrikant has separate procedure-level and individual-lot routes.

Procedure-level:

`/v2/trades/procedure/view/{procedure_token}`

Individual lot:

`/v2/trades/procedure/lot/view/{procedure_token}/{lot_token}`

Public Fabrikant pages and official user documentation show that a procedure can contain a separate lot part and that lot-level fields include initial price, application stages/dates, property information and related fields. Public procedure pages also exist with multiple separately numbered lots.

Therefore the previous one-token external ID could represent a procedure rather than an individual asset. A procedure-level price/status must not be copied to all lots.

A second root cause was field syntax. Fabrikant commonly renders money as:

`555 345,00 Рубль (RUB)`

or:

`22 162 770,03 Российский рубль (RUB)`

The generic money parser was not an adequate source contract for those labelled Fabrikant fields.

Fabrikant also exposes lifecycle text such as:

- `Прием заявок`;
- `Закончено (...)`;
- `Отказ организатора (...)`;
- `Рассмотрение заявок и подведение итогов`.

The generic status normalizer did not establish that source-specific lifecycle contract.

## Contract proven from official/public source structure

### Identity

An individual lot is accepted only when both tokens are present:

`fabrikant.ru:{procedure_token}:{lot_token}`

The separator is `:` because Fabrikant path tokens can contain `-` and `_`; concatenating two tokens with a hyphen is ambiguous.

A procedure route alone is no longer accepted as individual lot identity.

### Detail route

Canonical public detail route:

`https://www.fabrikant.ru/v2/trades/procedure/lot/view/{procedure_token}/{lot_token}`

Examples observed in public Fabrikant pages/search-indexed official content include the same two-token route shape.

### Price

Only explicitly labelled lot-level fields are accepted.

Accepted as `start_price`:

- `Начальная цена предмета договора`;
- `Начальная цена договора`;
- `Начальная цена лота`;
- a single top-level `Начальная цена` before a public-offer reduction schedule, with an explicit RUB marker.

Accepted as `current_price` only when Fabrikant explicitly labels `Текущая цена`.

Not used as price fallback:

- `Размер задатка`;
- `Шаг аукциона`;
- a neighbouring amount;
- the price of another lot;
- public-offer stage prices as a guessed current price.

For public offer, stage schedule prices are not promoted to `current_price` unless a separate source-specific current-stage contract is proven.

### Status

Source-specific mapping is fail-closed:

- `Прием заявок` -> `active`;
- `Приостановлено` -> `paused`;
- `Закончено` -> `closed`;
- `Отказ организатора` -> `closed`;
- `Рассмотрение заявок ...` -> `unknown` unless another explicit terminal marker exists.

An application deadline is never interpreted as proof that the auction is closed.

### Dates

The detail parser reads explicit labels for:

- application deadline: `Дата и время окончания приема заявок`;
- auction start: `Дата и время начала аукциона`.

### Property fields

The source-specific detail parser uses the structured Fabrikant label `Категория объекта` before text fallback.

Classification currently separates at least:

- `land`;
- `real_estate`;
- `vehicle`;
- `receivables`;
- `other`.

It also extracts, when explicitly present:

- title from `Предмет договора`;
- address from labelled `Адрес:`;
- cadastral number;
- area, including the Fabrikant form `Общая площадь, м²: 57,90`.

Public Fabrikant pages demonstrate mixed categories, including real estate, land, vehicles, equipment and metal scrap. The source must therefore not assume that every bankruptcy-sale item is real estate.

## Catalogue resolution implemented

The search catalogue still starts from:

`https://www.fabrikant.ru/procedure/search/sales?section_ids%5B%5D=6&page_limit=10`

but catalogue cards are treated as procedure candidates only.

For every catalogue procedure the connector now:

1. fetches the public procedure page read-only;
2. requires at least one canonical individual-lot link;
3. resolves each unique `procedure_token + lot_token`;
4. fetches the individual lot detail;
5. emits only the independently identified lot.

If any observed catalogue procedure does not expose a canonical individual lot route, the page fails closed with `SourceSchemaError` instead of publishing a procedure as a lot or copying another lot's fields.

## Regression tests

Added `tests/test_fabrikant_source.py` covering:

- procedure route rejected as lot identity;
- two-token stable identity;
- explicit initial price vs deposit/auction step;
- public-offer schedule does not overwrite initial/current price;
- active and terminal status labels;
- review stage remains unknown instead of inferred terminal;
- title/category/address/cadastre/area/dates;
- catalogue procedure -> canonical lot -> detail resolution;
- fail-closed behaviour when no individual lot route exists.

Dedicated sanitized fixtures were added under `tests/fixtures/fabrikant/`.
The shared `tests/test_etp_sources.py` fixture remains unchanged; source-specific
regressions are exercised only by `tests/test_fabrikant_source.py`.

### Test evidence

Pre-isolation verification on head `6af87cc07cf8a98435c14b1ab268ee4ee596e586`:

- focused Fabrikant + existing ETP tests: **90 passed**;
- applicable full SQLite CI: **1,497 passed, 11 deselected, 0 failed**.

An earlier full-CI run found five failures in the new Fabrikant tests caused by
an incorrectly grouped RUB regular-expression alternative. The cause was fixed
rather than weakening a guard.

After source isolation, the shared-registry assertions were removed from the
Fabrikant test file and replaced with a local Fabrikant profile plus sanitized
fixtures. Exact-head focused tests and applicable full CI must be green again
before this PR is ready for review; historical green runs are not substituted
for that final-head requirement.

## 2026-11-09 live read-only retest

A source-specific probe was added:

`scripts/audit_fabrikant_readonly.py`

Workflow:

`.github/workflows/etp-fabrikant-readonly.yml`

The probe is GET-only and does not use authentication, cookies, provider writes, BankrotAI DB writes, reconciliation or archival.

It is designed to prove:

- catalogue pages;
- procedure IDs;
- selected filters plus available status/section/type controls;
- public form method/action contract (sanitized path only);
- visible pagination;
- terminal condition;
- two full passes and ID digest stability;
- procedure -> individual-lot resolution;
- unique lot IDs;
- unresolved procedure count;
- up to 30 individual lot details;
- money/status/date labels.

### Transport evidence

A GitHub-hosted live attempt on 2026-11-09 timed out reaching
`www.fabrikant.ru` before the first catalogue response. This is treated as a
runner/network limitation, not as proof that the source is unavailable.

Trusted Home workflow run `42548839977` passed its unit job (**90 tests**) but
its Home audit job `128939946463` was cancelled by a newer branch push before
any Fabrikant request executed. It therefore provides **no live catalogue
evidence**.

The final isolated workflow runs the GET-only probe directly from the trusted
Home network, without entering the production worker/container and without DB,
scheduler, reconciliation, archival or map access. A completed exact-head Home
run is still required before live completeness can be claimed.

## What is NOT yet proven

The following must remain explicitly **NOT PROVEN** until the trusted Home live probe actually completes:

- whether `section_ids[]=6` alone is the correct current *active* bankruptcy-sale filter; the live probe now records the available/selected filter controls so this is decided from the current HTML rather than guessed;
- the real active catalogue size on 2026-11-09;
- whether the previous 9 observed procedure candidates are the whole active catalogue;
- page 2;
- terminal/last page;
- two-pass live stability/digest;
- current number of unique individual lot IDs;
- a current 30-detail sample, or all current lots if fewer than 30;
- live current price/status success rates;
- a source-total reconciliation if the UI exposes a declared total.

Accordingly this PR must remain draft/not production-accepted until that evidence is available.

## Changed files

Source-specific files only:

- `src/bankrotai/connectors/etp_sources/fabrikant.py`;
- `tests/test_fabrikant_source.py`;
- `tests/fixtures/fabrikant/catalogue_page.html`;
- `tests/fixtures/fabrikant/procedure_page.html`;
- `tests/fixtures/fabrikant/detail_active.html`;
- `tests/fixtures/fabrikant/detail_public_offer.html`;
- `scripts/audit_fabrikant_readonly.py`;
- `.github/workflows/etp-fabrikant-readonly.yml`;
- `docs/ETP_FABRIKANT_AUDIT_2026-11-09.md`.

Shared/integration-owned files have **zero diff** relative to current `main`:

- `src/bankrotai/connectors/etp_sources/common.py`;
- `src/bankrotai/connectors/etp_sources/pagination.py`;
- `src/bankrotai/connectors/etp_sources/profiles.py`;
- `src/bankrotai/connectors/etp_sources/source_specific.py`;
- `src/bankrotai/connectors/registry/direct_etp_catalogue.py`;
- `src/bankrotai/connectors/etp_sources/__init__.py`;
- `tests/test_etp_sources.py`;
- `docs/ROADMAP.md`;
- `docs/ETP_PARSER_STAGE_CHECKLIST.md`;
- scheduler / allowlist / reconciliation / archival configuration.

## Integration hook required

The isolated module is intentionally not registered by this PR.

A later integration PR should make only the minimal shared wiring:

1. In `profiles.py`, change only the Fabrikant profile from the one-token
   procedure route to the proven two-token lot route
   `/v2/trades/procedure/lot/view/{procedure_token}/{lot_token}`, preserving
   `page_number`, allowed hosts and production-disabled state.
2. In the ETP source registry/dispatcher, bind `fabrikant.ru` to
   `fabrikant.FabrikantConnector` with that profile.
3. No changes to generic money/status/category semantics are required.

Until that integration is reviewed centrally, current production behavior is
unchanged.

## Isolation review

Source-specific module:
`src/bankrotai/connectors/etp_sources/fabrikant.py`

Shared files modified:
**NONE**

Source-specific logic remaining in `common.py`:
**NONE**

Source-specific logic remaining in `pagination.py`:
**NONE**

Source-specific logic remaining in `profiles.py`:
**NONE**

Source-specific logic remaining in `source_specific.py`:
**NONE**

Source-specific logic remaining in `direct_etp_catalogue.py`:
**NONE**

Integration hook required:
minimal Fabrikant profile + registry binding described above.

Existing work preserved:
**YES**

Live evidence preserved:
**YES** — failed/timed-out/cancelled attempts are retained and are not promoted
to successful evidence.

Regression tests preserved:
**YES**

PRODUCTION ENABLED:
**NO**

## Acceptance state

Code-side false-positive risk is reduced: a procedure is no longer published as an individual Fabrikant lot, and unsupported price/status cases fail closed.

Live catalogue completeness is not yet accepted.

**PRODUCTION ENABLED: NO**
