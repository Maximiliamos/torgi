# Sberbank-AST bankruptcy source audit — 2026-11-09

Source: `sberbank-ast.ru`  
Agent scope: Sberbank-AST only  
Original audit base: `5997d239a71e253b7dd3386820ca08002f63f071`  
Final clean integration base: `37e4a71c0317de85fa2249f58ebaafc9239956e2`  
Production enabled: **NO**

## Previous unsafe state

The existing connector already POSTed to:

`https://www.sberbank-ast.ru/api/Processing/main`

with the public-client request shape for `UtpESGoodsList`, including
`pagesize=21`, zero-based `pagenum` and
`sourceTerm=Продажа имущества (предприятия) банкротов`.

The blocker was verification, not request construction. The old parser trusted the
requested filter but did not verify that returned rows actually belonged to bankruptcy.
It also classified rows from free text and treated API `Total` as a source total even
at the public display cap.

This allowed two unsafe interpretations:

1. an unfiltered/privatization response could be accepted as bankruptcy;
2. `Total=10000` could be treated as a real terminal source total.

## Official bankruptcy entry

The source profile now starts from the dedicated public bankruptcy route:

`https://utp.sberbank-ast.ru/Bankruptcy/NBT/Index/0/0/0/0`

The catalogue itself remains the public `ESGoodsList` API contract below.

## Verified catalogue request contract

Read-only request:

`POST https://www.sberbank-ast.ru/api/Processing/main`

Required request fields:

- `windowCode=/ESGoodsList`
- `actionType=MONITOR`
- `actionCode=default`
- `parm=es`
- XML `filterBody`
- `targetPageCode=UtpESGoodsList`
- `pagesize=21`
- zero-based `pagenum`
- `sourceTerm=Продажа имущества (предприятия) банкротов`

The request is built by
`src/bankrotai/connectors/etp_sources/sberbank_ast_contract.py` and is used by the
actual registered connector, not only by tests.

## Bankruptcy proof — fail closed

Every returned row must prove both provider fields:

- `ObjectSource == "Продажа имущества (предприятия) банкротов"`
- `SourceTradeSectionId == "13"`

A missing or mismatching marker fails the entire page closed. A row from
`Приватизация, аренда и продажа прав` is not silently accepted or treated as
bankruptcy.

The negative regression is exercised both directly against the isolated contract and
through `SOURCE_CONNECTOR_TYPES["sberbank-ast.ru"]`.

## Structured property classification

The bankruptcy catalogue can contain different asset types. TORGI accepts a property
row only when the provider's structured category proves:

`BranchGroup == "Недвижимое имущество"`

Free text is not allowed to override this field. A row whose title says
"земельный участок" but whose structured branch is transport is rejected from the
property result set. Conversely, a structured real-estate row is classified as
`real_estate` even if free text contains vehicle wording.

## Stable procedure ID and individual lot ID

Individual identity comes from the exact trusted GoodsView URL:

`https://utp.sberbank-ast.ru/Catalog/NBT/GoodsView/<a>/<b>/<c>/<goods_id>`

External ID:

`sberbank-ast.ru:<a>-<b>-<c>-<goods_id>`

Only HTTPS on `utp.sberbank-ast.ru` is accepted. Query strings, fragments,
credentials, alternate ports and untrusted hosts are rejected.

Sberbank-AST multi-lot codes such as:

- `SBR013-2607300003-0.1`
- `SBR013-2607300003-0.2`

are split into:

- procedure: `SBR013-2607300003-0`
- individual source lot code: full value with `.N`
- individual lot number: `N`

Regression tests verify that two lots of one procedure keep distinct GoodsView IDs and
distinct prices. A duplicate GoodsView identity on a page fails closed.

## Price contract

Catalogue initial price is accepted only from:

1. `purchAmountRUB`; or
2. `purchAmount` when currency is explicitly `RUB` or `643`.

No catalogue current price is invented.

GoodsView detail parsing distinguishes labelled:

- initial price;
- current price;
- deposit;
- auction/trade step.

Public-offer schedule/current-price semantics are not inferred beyond fields explicitly
present on the individual GoodsView page.

## Lifecycle and dates

Sberbank-AST lifecycle text is normalized to TORGI canonical states:

- `scheduled`
- `active`
- `paused`
- `closed`
- `unknown`

Final states win over historic application text left visible on completed pages.
"Приём заявок завершён" alone remains `unknown`; it is not treated as a completed
auction.

Catalogue:

- `RequestStopDate` -> application deadline;
- `AuctionResultDate` is deliberately **not** mapped to auction start.

GoodsView detail can prove:

- application start;
- application deadline;
- auction/trade date.

## GoodsView detail contract

The registered connector derives GoodsView from the exact catalogue external ID and
re-verifies identity before accepting detail HTML.

A generic/informational shell is rejected. A valid individual property card can provide:

- initial/current price;
- status;
- application start/end;
- auction date;
- address;
- cadastral number;
- area;
- deposit;
- step;
- bankruptcy case number;
- procedure/lot code;
- public-offer marker when explicitly visible.

The detail parser is wired into
`SOURCE_CONNECTOR_TYPES["sberbank-ast.ru"].fetch_lot()` and the source exposes
`detail_enrichment`.

## Pagination and the 10,000 display cap

Page size is fixed to the observed public contract: 21.

For `Total < 10000`:

- terminal page is `ceil(Total / 21)`;
- every non-terminal page must contain exactly 21 raw provider rows;
- terminal page must contain the exact remainder;
- a page beyond terminal is rejected;
- full-walk raw-row count must therefore equal API `Total`.

For `Total >= 10000`:

- `Total` is recorded only as `api_total_reported`;
- it is **not** exposed as `total_items`;
- `api_total_is_source_end=false`;
- `terminal_page=None`;
- short pages before a proven source end fail closed;
- reaching the display boundary raises an error instead of claiming completeness.

Therefore completeness above the display cap remains **NOT_PROVEN** until an official
beyond-cap paging/search mechanism is established.

Missing-lot reconciliation and archival remain disabled.

## Two-pass live evidence probe

`scripts/probe_sberbank_ast_live_raw.py` is standalone and can be piped into the
trusted Home worker without installing the branch into production.

It performs no database writes and no authenticated/provider mutations.

When `Total < 10000` it:

- walks every catalogue page twice;
- requires both full walks to have the same API total and ordered page digests;
- requires each walk's raw-row count to equal API `Total`;
- checks the exact terminal-page remainder;
- collects unique property GoodsView IDs;
- checks up to 30 individual GoodsView details;
- records sanitized field-presence evidence and multi-lot examples.

When the source reports the public display cap, it remains fail-closed and reports
completeness as not proven.

A second operator script,
`scripts/audit_sberbank_ast_bankruptcy_readonly.py`, exercises the isolated
source contract directly with `--passes 2 --details 30 --full`.

## CI

`.github/workflows/etp-sberbank-ast-readonly-contract.yml` contains three checks:

1. exact-head isolated source-contract tests **plus registry integration tests**;
2. trusted Home read-only live contract;
3. GitHub-hosted public-network probe as a non-authoritative diagnostic fallback.

The Home job is authoritative for live acceptance. The hosted fallback may be blocked by
provider/network policy and is therefore diagnostic only.

## Regression coverage

The final branch covers:

- canonical bankruptcy entry route;
- exact bankruptcy request filter;
- missing/wrong bankruptcy markers -> fail closed;
- privatization response -> fail closed through the real registry connector;
- structured property classification precedence;
- trusted GoodsView identity;
- duplicate GoodsView identity;
- procedure/individual-lot split for `.N` codes;
- independent prices for multi-lot procedures;
- explicit RUB price rules;
- canonical lifecycle precedence;
- application start/end and auction date;
- address/cadastre/area;
- generic GoodsView shell rejection;
- catalogue/detail identity mismatch;
- API Total/page row-count consistency;
- 10,000 display cap not exposed as source end;
- real registry search, healthcheck and fetch_lot integration.

## Final branch diff scope

Required integration files:

- `src/bankrotai/connectors/etp_sources/common.py`
- `src/bankrotai/connectors/etp_sources/profiles.py`
- `src/bankrotai/connectors/etp_sources/sberbank_ast_contract.py`
- `tests/test_etp_sources.py`
- `tests/test_sberbank_ast_contract.py`
- `tests/fixtures/sberbank_ast/catalogue_bankruptcy_real_estate.json`
- `tests/fixtures/sberbank_ast/goodsview_detail.html`
- `scripts/audit_sberbank_ast_bankruptcy_readonly.py`
- `scripts/probe_sberbank_ast_live_raw.py`
- `.github/workflows/etp-sberbank-ast-readonly-contract.yml`
- this audit document.

No scheduler, production allowlist, missing-lot reconciliation, archival,
`docs/ROADMAP.md`, or global parser checklist is enabled/changed by Agent 12.

## Acceptance state

Code contract: **IMPLEMENTED**  
Registry integration: **IMPLEMENTED**  
Negative bankruptcy regression: **IMPLEMENTED**  
10,000 fail-closed behavior: **IMPLEMENTED**  
Production scheduling: **DISABLED**  
Missing-lot reconciliation: **DISABLED**  
Production archival: **DISABLED**

Fresh live counts are recorded in the PR body from the exact-head workflow; this document
does not fabricate them before the workflow completes.

**PRODUCTION ENABLED: NO**
