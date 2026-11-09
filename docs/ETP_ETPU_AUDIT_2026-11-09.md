# ETPU source-contract audit — 2026-11-09

Source: Уральская электронная торговая площадка (УЭТП), `etpu.ru`  
Public bankruptcy host: `https://bankrupt.etpu.ru`  
Base SHA at start: `34baeaf7202b190e83c1aeaf08ec46f2efa2da76`  
Production enablement: **NO**

## Scope and old state

The pre-change repository evidence classified ETPU as:

- catalogue: PASS;
- two bounded catalogue pages / 40 candidates: PASS;
- individual detail: PASS;
- status: PASS;
- individual price: FAIL;
- property classification: FAIL;
- terminal page: NOT PROVEN.

This PR is intentionally source-scoped. It does not change the scheduler,
production allowlists, reconciliation, archival, `docs/ROADMAP.md`, or the
global ETP parser checklist.

## Root causes

### Price

The shared detail parser expects a money value that is itself followed by a
ruble marker. ETPU renders an explicit per-lot field in the opposite layout:

`Начальная цена, руб.: <amount>`

Public-offer cards may additionally expose:

`Текущая цена, руб.: <amount>`

Deposit, auction step and interval prices are separate fields. Falling back to
an adjacent amount would therefore be unsafe.

The ETPU parser now accepts only the explicit labelled per-lot start/current
price fields and leaves a missing value null. It does not infer a price from a
deposit, step, interval or procedure-level amount.

### Property classification

The generic parser inferred category from free text. ETPU exposes a structured
`Классификатор ЕФРСБ` block on individual lot cards, so free-text inference
was not an adequate source contract.

The source-specific parser now preserves:

- the raw EFRSB classifier text;
- all normalized classifier flags;
- a conservative single normalized category.

Observed/covered classes include real estate, land, ownership shares, lease
rights, vehicles, equipment and receivables. Structured source data wins over
free text. A real observed edge case is an automobile whose official classifier
is `Прочее`; the parser preserves `other` instead of silently overriding the
provider classification from the title.

## Official public contract observed on 2026-11-09

Catalogue:

`https://bankrupt.etpu.ru/public/purchases-all/`

The public catalogue exposes filters for lot/trade number, lot/trade name,
property text, initial price, trade type, status, organizer and debtor. It is a
mixed catalogue, not a property-only endpoint.

Individual detail identity:

`/public/{auctions|contests|public-offers}/lots/view/{lot_id}/`

Stable parser identity remains:

`etpu.ru:{sale_type}-{lot_id}`

This matters for multi-lot procedures: separate lots under one procedure have
separate lot-detail URLs and separate prices.

The public catalogue uses ASP.NET postback pagination. Existing repository
evidence had already proved page 2 distinct from page 1. The source-specific
probe added by this PR walks the postback chain sequentially, refuses repeated
page fingerprints, requires a provider terminal page, and performs two complete
passes before reporting a matching contract.

## Detail sample

Thirty distinct official lot-detail URLs were checked read-only on 2026-11-09:

- `public-offers-143438`
- `auctions-143436`
- `auctions-143434`
- `auctions-143435`
- `auctions-143432`
- `public-offers-143422`
- `auctions-143420`
- `auctions-143418`
- `auctions-143416`
- `auctions-143415`
- `auctions-143426`
- `public-offers-143444`
- `auctions-143409`
- `auctions-143410`
- `auctions-143406`
- `auctions-143399`
- `auctions-143397`
- `public-offers-143395`
- `auctions-143400`
- `public-offers-142805`
- `public-offers-143362`
- `public-offers-143363`
- `auctions-143411`
- `auctions-143299`
- `auctions-141574`
- `auctions-143146`
- `public-offers-142400`
- `public-offers-142750`
- `public-offers-142445`
- `public-offers-142839`

Observed field success in this bounded sample:

| Field | Result |
| --- | ---: |
| Explicit initial price | 30 / 30 |
| Explicit status | 30 / 30 |
| Structured EFRSB classifier | 30 / 30 |
| Individual detail URL / lot identity | 30 / 30 |

Public-offer samples also demonstrate that initial and current price are
separate fields. The implementation does not populate `current_price` unless
the explicit current-price label is present.

## Pagination status

**Terminal page is not claimed as proved by this document yet.**

The browser-readable catalogue evidence available during this audit exposed the
official numbered pagination and existing repository evidence proved page 2,
but the audit environment could not execute the provider's live ASP.NET
postback chain to its end: direct container DNS access to
`bankrupt.etpu.ru` was unavailable, while the read-only web renderer exposes a
cached HTML representation rather than a stateful ASP.NET form session.

To prevent a false PASS, this PR adds:

`python scripts/etpu_readonly_audit.py --details 30`

The probe is source-specific and read-only. It:

1. walks pages sequentially through the existing provider postback contract;
2. requires a real terminal page rather than a fixed maximum;
3. rejects duplicate IDs / repeated page bodies;
4. performs two complete passes;
5. compares ID and catalogue-record digests;
6. fetches up to 30 individual details;
7. reports identity, initial-price, status and classifier success rates;
8. returns a non-zero exit code if the full contract is not proved.

Until that probe completes on a network that can reach the provider,
`complete_source_inventory`, reconciliation, archival and production
acceptance remain false.

## Code changes

- `src/bankrotai/connectors/etp_sources/etpu.py`
  - isolated ETPU catalogue row/page parser;
  - stable `sale_type + lot_id` identity;
  - ASP.NET postback pagination, including the provider's ten-page `>>` block transition;
  - explicit initial/current price parsing;
  - deposit and auction-step parsing kept separate from lot price;
  - public-offer interval schedule extraction;
  - explicit lifecycle/status normalization;
  - Moscow-time dates;
  - structured EFRSB classifier extraction and conservative mapping;
  - address/cadastre/area/VIN property extraction;
  - source-specific fail-closed exceptions and repeated-page fingerprints;
  - direct read-only client for catalogue/detail proof.
- `tests/test_etpu_contract.py`
  - imports the isolated ETPU module directly;
  - regression coverage for price isolation, current price, deposit/step separation,
    public-offer intervals, seven required classifier groups, property fields,
    multi-lot identity, block pagination and exact detail URL reconstruction.
- `scripts/etpu_readonly_audit.py`
  - imports the isolated ETPU module directly;
  - two-pass terminal-pagination/detail audit with fail-closed exit status.
- `.github/workflows/etpu-source-contract-hosted.yml`
  - GitHub-hosted public read-only proof.
- `.github/workflows/etpu-source-contract-readonly.yml`
  - Home-runner read-only proof using branch code in a disposable container.
- `docs/ETP_ETPU_AUDIT_2026-11-09.md`
  - this evidence record.

The integration-owned shared files are intentionally not modified by the final
source-isolation diff.

## Integration hook required

A later integration PR must connect the isolated module to the shared
dispatcher. The minimum required wiring is:

- shared ETP registry/dispatcher recognizes `source_id == "etpu.ru"`;
- catalogue search calls `etpu.EtpuClient.fetch_catalogue_page(...)` or an
  equivalent adapter around `etpu.parse_catalogue_page(...)`;
- individual detail calls `etpu.EtpuClient.fetch_detail(...)` or
  `etpu.parse_detail(...)`;
- no generic money/status/category fallback is required or desired for ETPU.

No production scheduler, reconciliation, archival or allowlist change belongs
to this source PR.

## Isolation review

Source-specific module:

`src/bankrotai/connectors/etp_sources/etpu.py`

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

The shared dispatcher must later register `etpu.py` for `etpu.ru` and route
catalogue/detail calls to that module. No other shared behaviour is required.

Existing work preserved:

YES

Live evidence preserved:

YES

Regression tests preserved:

YES

PRODUCTION ENABLED:

NO

## Explicitly not proved / not changed

Not proved by the current environment:

- current provider terminal page and current total;
- two complete live catalogue digests;
- complete property-only inventory, because the official catalogue is mixed;
- universal address/cadastre/area completeness across every class.

Not changed:

- production scheduler;
- source allowlist;
- reconciliation;
- missing-lot archival;
- global roadmap/checklist;
- production database or map publication.

**PRODUCTION ENABLED: NO**
