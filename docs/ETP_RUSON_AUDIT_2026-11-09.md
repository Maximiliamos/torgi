# RUSSIA OnLine audit — 2026-11-09

## Scope

Source: rus-on.ru / RUSSIA OnLine.

This PR continues the existing work from PR #1177. It does not enable the source in production and does not change reconciliation, archival, the scheduler, the production allowlist, the general ETP checklist, or the roadmap.

Initial exact base used when the work started: 34baeaf7202b190e83c1aeaf08ec46f2efa2da76.

## Previous state

PR #1166 fixed production reports for known RUSSIA OnLine lots, including labelled initial prices without a mandatory RUB suffix and ended status/detail enrichment. That was evidence for the reported examples, not proof of the whole source class.

## Root causes addressed in this PR

1. Public-offer detail pages publish the effective current price in the period table labelled "Цена на периоде", not necessarily in a scalar field labelled "Текущая цена".
2. Whole-page text classification can be contaminated by generic page labels such as "Является объектом недвижимости"; source category and the individual lot description must be scoped before classification.
3. RUSSIA OnLine pagination is form-driven with pagenum_send(...) controls and a tradesearch form. Terminal-page proof must be source-specific and fail closed on repeated/non-sequential pages.
4. Legacy detail pages and current structured table layouts both exist and need independent regression coverage.

## Source-specific implementation

All new RUSSIA OnLine parsing and read-only navigation added by this PR live in:

src/bankrotai/connectors/etp_sources/ruson.py

The module owns catalogue lot_nid extraction and stable IDs; catalogue/detail identity verification; RUSSIA OnLine form pagination and terminal detection; read-only navigation; price/public-offer schedule parsing; deposit/auction step; lifecycle dates/status; title/address/cadastre/area; classification; layout variants; and source-specific fail-closed errors.

## Fixtures and regression tests

Fixtures cover ordinary auction, active public offer, ended public offer, prices with and without RUB, integer/decimal dot/decimal comma values, public-offer kopecks, and a vehicle page containing real-estate boilerplate. The historical #1166 no-RUB case is retained as a direct source-module regression.

## Read-only live audit

scripts/ruson_readonly_audit.py and src/bankrotai/services/etp_ruson_contract_audit.py first bound and prove the terminal page with direct page-number probes, then repeat a deterministic sample of catalogue pages twice, rejecting duplicate or unstable IDs. The same audit verifies at least 30 detail pages when the gate passes and covers active/ended, ordinary/public-offer, price/status/dates and public-offer current prices. It does not claim two full catalogue passes.

The audit performs no database writes, no archival, no reconciliation, and no map publication.

## Live evidence status

Pending the exact-head source-specific GitHub workflow after source isolation and update from current main. Synthetic fixtures are not treated as acceptance evidence.

## Integration hook required

No shared wiring is introduced by this PR.

A later integration PR should minimally route rus-on.ru detail enrichment from src/bankrotai/connectors/etp_sources/common.py to ruson.parse_detail_html(...) or the equivalent source adapter entry point while preserving the existing generic identity/merge guards. No other shared behaviour is required by this source module.

## Isolation review

Source-specific module:
src/bankrotai/connectors/etp_sources/ruson.py

Shared files modified by final PR diff:
NONE

Source-specific logic introduced by this PR remaining in common.py:
NONE. Existing rus-on baseline logic already present in main from #1166 is integration-owned and intentionally not modified here.

Source-specific logic introduced by this PR remaining in pagination.py:
NONE

Source-specific logic introduced by this PR remaining in profiles.py:
NONE

Source-specific logic introduced by this PR remaining in source_specific.py:
NONE. Existing #1166 baseline helper in main is not changed by this PR.

Source-specific logic introduced by this PR remaining in direct_etp_catalogue.py:
NONE

Integration hook required:
YES — minimal dispatcher wiring in a later integration PR as described above.

Existing work preserved:
YES

Live evidence preserved:
YES

Regression tests preserved:
YES

PRODUCTION ENABLED:
NO
