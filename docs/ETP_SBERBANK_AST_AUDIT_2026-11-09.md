# Sberbank-AST bankruptcy source audit — 2026-11-09

Source: `sberbank-ast.ru`  
Scope: Agent 12 only  
Base SHA: `5997d239a71e253b7dd3386820ca08002f63f071`  
Production enabled: **NO**

## Previous state

The repository had a Sberbank-AST profile pointing at `https://www.sberbank-ast.ru/ESGoodsList.html`
and a JSON POST implementation for `/api/Processing/main`. The request already sent
`sourceTerm=Продажа имущества (предприятия) банкротов`, but the response parser did
not prove that the provider actually applied that filter.

The 2026-11-06 browser evidence explicitly recorded that:

- the bankruptcy checkbox was unchecked in observed `ESGoodsList.html` tabs;
- visible cards were unfiltered or belonged to privatization / lease / sale of rights;
- `SaleBankruptProperty.html` is an informational landing, not proof of a filtered catalogue;
- the client contract uses `targetPageCode=UtpESGoodsList`, zero-based `pagenum`,
  default `pagesize=21`, `PurchaseList`, `Total`, and `BranchGroup`;
- the public UI caps displayed total rows at about 10,000;
- a bankruptcy-filtered API response, page 2, terminal page and a representative
  GoodsView detail sample were still NOT_TESTED.

## Root cause

The connector trusted the requested `sourceTerm` instead of verifying the returned rows.
A successful HTTP response containing a valid-looking GoodsView URL could therefore be
accepted even if it came from another Sberbank-AST section.

The old `healthcheck()` could report `ok` for such a response.

The old pagination logic also treated `Total=10000` as an ordinary source total and would
eventually stop there, even though the public client is known to cap visible totals around
10,000. That could turn a display limit into a false terminal-page proof.

## Contract established in code

### Catalogue request

Read-only endpoint:

`POST https://www.sberbank-ast.ru/api/Processing/main`

Request contract retained from the public client implementation:

- `windowCode=/ESGoodsList`
- `actionType=MONITOR`
- `actionCode=default`
- `parm=es`
- XML `filterBody`
- `targetPageCode=UtpESGoodsList`
- `pagesize=21`
- zero-based `pagenum`
- `sourceTerm=Продажа имущества (предприятия) банкротов`

### Bankruptcy proof

A catalogue row is accepted only if the returned provider fields prove:

- `ObjectSource == "Продажа имущества (предприятия) банкротов"`
- `SourceTradeSectionId == "13"`

If either marker is absent or different, parsing fails closed. A privatization row is not
silently skipped as if it were valid bankruptcy output; the entire response is considered
unverified.

### Property scope

The bankruptcy catalogue is mixed. Real estate is accepted only when the provider's
structured field says:

`BranchGroup == "Недвижимое имущество"`

Free text is not used as a substitute for this classification. Bankruptcy vehicles,
equipment and other branches may be traversed for pagination but are not accepted as
property lots by this source profile.

### Lot identity

Accepted catalogue rows must contain a trusted HTTPS URL matching:

`https://utp.sberbank-ast.ru/Catalog/NBT/GoodsView/<a>/<b>/<c>/<goods_id>`

The complete four-component GoodsView path is used in the external identity. Duplicate
GoodsView identity on one API page fails closed.

A regression test covers two rows with the same procedure number and different GoodsView
IDs/prices, proving that prices are not copied between lots.

### Price and status

Catalogue initial price is accepted only from:

1. `purchAmountRUB`; or
2. `purchAmount` when currency is explicitly `RUB` or `643`.

No current price is invented from the catalogue response.

Status comes only from the provider status fields already used by the source-specific
normalizer. `AuctionResultDate` is not treated as auction start.

### Dates and detail

`RequestStopDate` maps to application deadline.

Individual detail uses the GoodsView URL derived from the exact catalogue identity.
The detail parser now verifies that the URL identity matches the requested external ID
and rejects generic shells that do not contain enough lot/property evidence.

On a valid GoodsView page the parser can extract labelled initial/current price, status,
application deadline, auction date, address, cadastral number and area. These fields remain
fail-closed/null when the page does not prove them.

## Pagination and the 10,000 cap

`Total < 10000` can be used to derive the terminal page under the current API contract.

`Total >= 10000` is **not** accepted as source end. When traversal reaches the display
boundary, the connector raises a schema error instead of claiming terminal pagination.

Therefore:

- source completeness above the display cap: **NOT_PROVEN**
- missing-lot reconciliation: **disabled**
- archival from this source: **disabled**

## Read-only probe

Added:

`scripts/audit_sberbank_ast_bankruptcy_readonly.py`

The probe:

- performs no persistence;
- checks page 1, page 2 and derived terminal page when `Total < 10000`;
- can walk all pages with `--full` only below the display cap;
- supports two passes and compares page-ID digests;
- can verify up to the requested number of individual GoodsView details;
- emits only a sanitized evidence summary;
- never enables reconciliation or production.

Recommended operator command:

`python scripts/audit_sberbank_ast_bankruptcy_readonly.py --passes 2 --details 30 --full`

## Live read-only recheck on 2026-11-09

The official public navigation still exposes the bankruptcy-property section. During this
agent session, direct retrieval of the dynamic `ESGoodsList.html` / filtered
`/api/Processing/main` response and GoodsView HTML was not available through the
read-only web environment.

Accordingly, this PR does **not** claim a new live filtered two-pass walk or 30 live
GoodsView details from 2026-11-09.

Current live evidence status:

| Check | Result |
| --- | --- |
| Official bankruptcy section exists | OBSERVED |
| Exact filtered API response captured on 2026-11-09 | NOT_PROVEN |
| Page 1 filtered bankruptcy response | NOT_PROVEN |
| Page 2 filtered bankruptcy response | NOT_PROVEN |
| Terminal page below cap | NOT_PROVEN |
| Source end above 10,000 | NOT_PROVEN by design |
| Two full read-only passes | NOT_PROVEN |
| 30 live GoodsView details | NOT_PROVEN |
| GoodsView identity schema | Supported by prior repository evidence and guarded in code |
| Production acceptance | NO |

The source-specific probe is the required mechanism for collecting the missing operator
evidence without weakening the parser.

## Regression coverage

Added/updated tests cover:

- returned bankruptcy markers are mandatory;
- privatization / wrong section fails closed;
- structured `BranchGroup` controls property scope;
- free-text property words cannot turn a vehicle branch into real estate;
- two lots of one procedure retain distinct GoodsView IDs and distinct prices;
- duplicate GoodsView ID fails closed;
- `Total=10000` is not terminal source end;
- request contains the bankruptcy source term;
- explicit RUB price precedence;
- GoodsView catalogue/detail identity match;
- labelled GoodsView price/current price/dates/cadastre/area extraction.

## Files changed by this agent

- `src/bankrotai/connectors/etp_sources/common.py`
- `src/bankrotai/connectors/etp_sources/sberbank_ast_contract.py`
- `tests/test_etp_sources.py`
- `tests/test_sberbank_ast_contract.py`
- `tests/fixtures/sberbank_ast/catalogue_bankruptcy_real_estate.json`
- `tests/fixtures/sberbank_ast/goodsview_detail.html`
- `scripts/audit_sberbank_ast_bankruptcy_readonly.py`
- `docs/ETP_SBERBANK_AST_AUDIT_2026-11-09.md`

No scheduler, production allowlist, reconciliation, archival, `docs/ROADMAP.md`, or
`ETP_PARSER_STAGE_CHECKLIST.md` change is included.

## Remaining limitations

1. A fresh official bankruptcy-filtered API capture is still required from an environment
   that can execute the public client request.
2. The read-only probe must be run against at least page 1, page 2 and terminal page, then
   two-pass digests compared.
3. At least 30 live GoodsView property details must be checked when available.
4. If the API reports 10,000 or more, an official paging/search mechanism beyond the public
   display cap must be established before completeness can be claimed.
5. Public-offer current-price semantics require live examples before any additional
   schedule-specific field mapping is added.
6. Production acceptance is a separate review.

**PRODUCTION ENABLED: NO**
