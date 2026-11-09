# ALFALOT source-contract audit — 2026-11-09

**Source:** `alfalot.ru`; **base:** `5997d239a71e253b7dd3386820ca08002f63f071`  
**Status:** BLOCKED / partial browser evidence only; **PRODUCTION ENABLED: NO**.

## Root cause in existing parser

- Generic profile at `src/bankrotai/connectors/etp_sources/profiles.py` selects `SUPCOMING` and `SSALE`, the upcoming-sales `LotList` hall, **not** the browser-confirmed active filter `stateFilter_GD`.
- Existing `supports_get_pagination=True` and `LotList.pageNum.lots` are unsupported assumptions for the actual active search: observed UI pager uses `href="#"` JavaScript actions. The real request method, endpoint, payload, page numbering and exact filter keys are **not captured**.
- The generic parser can parse candidate links but has no verified ALFALOT property-scope filter. Keyword matching is not proof of a sale/property inventory, nor does an `OfferCard` procedure identify a single `LotCard` lot.
- A historical shared test treated a synthetic `SUPCOMING` page-2 GET as evidence. It remains unchanged in integration-owned shared tests; the independent ALFALOT test suite now asserts that active GD HTTP import is blocked until proven.

## Evidence, strictly separated by date

**Archived operator browser evidence — 2026-11-06**, not fresh 2026-11-09 live proof: `docs/ETP_METS_ALFALOT_SBER_BROWSER_2026-11-06.md`.

| Observation | Proven on 2026-11-06 | Still missing |
| --- | --- | --- |
| Selected state | DOM `div#stateFilter_GD.filterItem.selected`; visible «прием заявки» | Actual raw HTTP/XHR request and server-side semantics |
| Page 1 | 20 links were visible | Identity list was not retained |
| Page 2 | 20 links observed | HTTP pagination body and filter preservation |
| Page 3 | 2 different links; `<b>3</b>`, disabled next controls | Independent network-backed source-terminal proof |
| IDs | 22 distinct links across pages 2–3 | 42/42 unique IDs over complete current walk, two matching passes |
| Details | `0026026025DS.lot1` and `0042926003DS.lot1`: 2/2 price/status | 30 fresh representative property details or all available |
| Prices | 56,000,000 RUB and 197,331,000 RUB respectively | Source-wide lot-specific price, current price, deposit/step separation |
| Status | Both «Идет прием заявок» | Server filter/status cross-check for all individual lots |
| Property | Second card: building 4,177.1 m², cadastre `78:12:0007135:1054` | Official structured property classification and exclusions |
| Procedure types | `BUYING`, `RFI`, `SMALL_PURCHASE`, `SALE`, `DEBTOR_SALE` all selected | Sale/property-only scope; mixed procedures excluded |

**Fresh read-only GitHub Actions evidence — 2026-11-09:** [workflow run 42550388590](https://github.com/Maximiliamos/torgi/actions/runs/42550388590), [sanitized artifact 13645192643](https://github.com/Maximiliamos/torgi/actions/runs/42550388590/artifacts/13645192643). At 02:21:53 UTC, first-party browser GWT UI rendered one selected `stateFilter_GD` labelled «прием заявки» with **20 individual LotCard links** on page 1. Clicking GD issued **GET `https://etp.alfalot.ru/searchServlet`** (XHR, HTTP **200**, `application/javascript`) with query **names** `filter`, `limit`, `query`, `sort`. Only names were retained, not raw values, so filter meaning, actual pageSize/offset and exact raw request remain **unverified**. Current paginator UI presents bold page `1`, clickable `2`, `>`, `>>` in `.pages`. This is fresh **page-1 UI and request-shape** proof, not a backend-filter or terminal/full-walk proof. Provider data not written to DB. Other live job failures are diagnostic and cannot be interpreted as acceptance.

The public detail identifiers above are **archived historical observations**, not fresh individual-detail verification.

## Public official individual-card cross-check (indexed pages, not current filtered catalogue)

The independent public search/browser retrieval on 2026-11-09 found first-party
individual card representations. The original publication/indexing time varies;
these are **not** claims about currently active lot status or 2026-11-09 GD pagination.

- https://etp.alfalot.ru/app/LotCard/page?LotCard.lotEntity=LT%3Acoreboa1bsm6o0000q1pk823cqa7he10
  shows opaque **LT:coreboa...** URL identity, displayed **0020626010DS.lot1**,
  labelled initial price **2,624,497.53 RUB**, status **Работа комиссии**,
  deposit **500,000 RUB**, bid step **131,224.88 RUB**. These fields have
  independent meanings and must not be interchanged.
- https://etp.alfalot.ru/app/LotCard/page?LotCard.lotEntity=LT%3Acoreboa1bsm6o0000praoiq4fm47876s
  shows labelled **65,000,000 RUB initial**, **50,375,000 RUB current
  minimum** with VAT annotations (public offer), plus a separate price
  reduction schedule. The schedule is not the current price field.
- https://etp.alfalot.ru/app/LotCard/page?LotCard.lotEntity=LT%3Acoreboa1bsm6o0000p9u664edrklm948
  and https://etp.alfalot.ru/app/LotCard/page?LotCard.lotEntity=LF%3Acoreboa1bsm6o0000pnu91etfppce4gk
  show distinct **LT:** and **LF:** first-party opaque entity prefixes; the
  latter original index URL carried a \`;jsessionid\` path suffix, which is
  never persisted by our canonicalization.
- https://etp.alfalot.ru/app/OfferCard/page?OfferCard.offerUUID=Scoreboa1bsm6o0000ps0krvk345mtvik
  is a procedure showing **Идет прием заявок**, while its ten parking-space
  lots are individually marked **Отказ от проведения**. **Procedure status
  MUST NOT be inherited by the individual lots.**
- https://etp.alfalot.ru/app/LotCard/page?LotCard.lotEntity=LT%3Acoreboa1bsm6o0000ppuqbpepb9d5dk8
  is an automobile, and another official indexed card
  https://etp.alfalot.ru/app/LotCard/page?LotCard.lotEntity=LF%3Acoreboa1bsm6o0000pnu91etfppce4gk
  is non-property merchandise. They confirm that sale procedures do not imply
  real-estate-only scope.

These examples support only bounded detail **field layouts**, not a current
filtered sample. Additional regression tests cover the observed variations.
The source-specific offline parser accepts explicitly labelled
\`Начальная (минимальная) цена договора (лота)\` and
\`Текущая минимальная цена договора (лота)\` independently, allowing the
source's VAT annotations. The procedure's fields are never copied into
an individual LotCard record.

## Changes in this PR

- `src/bankrotai/connectors/etp_sources/alfalot_audit.py`: source-only, independently importable parser/validators for individual LT/LF identity, two-pass IDs/digests, exact initial/current prices, per-lot statuses, property triage, explicit GD state and terminal controls. Exposes `require_verified_active_http_contract()` for centralized fail-closed integration.
- `scripts/probe-alfalot-readonly.py`: public Chromium GWT source-only read-only filter/pager diagnostic, sanitized request-response shapes, two-pass candidate support (fails closed on unknown pager), bounded individual details, no personal data or credentials retained.
- `tests/test_alfalot_audit.py`: isolated offline regression cases for identity, price/status isolation, GD scope, terminal controls, changed ID digests and multi-lot distinctions.
- `.github/workflows/etp-alfalot-live-readonly.yml`: ALFALOT-only focused tests and independently labelled live diagnostic job. The live diagnostic failure is never construed as source acceptance.

**Integration-owned files restored exactly to `main`:** `profiles.py` and `tests/test_etp_sources.py`. No changes to `common.py`, `pagination.py`, `source_specific.py` or `direct_etp_catalogue.py`; none of the existing root causes, evidence, tests or fixtures were discarded.

## Integration hook required

The existing `profiles.py` has a legacy `SUPCOMING` GET configuration that is **not** the validated GD contract. No new shared wiring is shipped in this PR. Until the centralized integrator proves official GD request values and property scope, the minimal safe hook is:

```python
# Central integration (future integration-owned PR only):
from bankrotai.connectors.etp_sources.alfalot_audit import require_verified_active_http_contract
# In the ALFALOT path of HtmlEtpConnector._request before legacy pagination:
require_verified_active_http_contract()  # fails closed
```

After full verified HTTP contract, the integrator may replace that guard by delegating ALFALOT `parse_listing_html` / detail / paginator operations to `alfalot_audit.py`. Avoid altering shared generic semantics or invoking the old unverified URL as a production inventory. **Current PR does not enable or schedule ALFALOT.**

## Isolation review

- Source-specific module: `src/bankrotai/connectors/etp_sources/alfalot_audit.py`.
- Shared files modified: **NONE**.
- Source-specific logic remaining in `common.py`: **NONE added by PR** (existing legacy generic behaviour predates it).
- Source-specific logic remaining in `pagination.py`: **NONE added by PR**.
- Source-specific logic remaining in `profiles.py`: **NONE added by PR** (legacy unchanged).
- Source-specific logic remaining in `source_specific.py`: **NONE added by PR**.
- Source-specific logic remaining in `direct_etp_catalogue.py`: **NONE added by PR** (legacy unchanged).
- Integration hook required: **YES**, specified above; integrator-owned.
- Existing work preserved: **YES**.
- Live evidence preserved: **YES**, archived original and sanitized 2026-11-09 run.
- Regression tests preserved: **YES**, now source-specific.
- **PRODUCTION ENABLED: NO**.

## Required remaining operator verification

1. In a normal public Chromium session on 2026-11-09+, record **redacted raw request/response** on `stateFilter_GD` click and compare against `все` / `SUPCOMING`; explicitly prove server-side active-application status, method, path, request fields, pageSize, page index, total.
2. Repeat page 1, page 2 and source terminal by the **same raw backend contract** (not URL guesses). Complete **two** read-only walks, compare sorted individual lot IDs and catalogue record digests. Capture sample size and exact timestamps.
3. Establish whether catalogue rows are individual lots or procedures. For multi-lot procedures, verify distinct `LotCard.lotEntity` and visible lot identifiers. Do not transfer price/status/title across lots.
4. Prove official structured sale/property categories and exclude `BUYING`, `RFI`, `SMALL_PURCHASE`, autos, equipment, claims and rental where out of scope; mark ambiguous as rejected.
5. Inspect **at least 30 property cards if available**, otherwise every distinct eligible card, reporting per-field counts/rates for identity, title, initial/current price, status, application dates, address, cadastre, area and property type; compare listing→detail per ID.
6. Update ALFALOT parser and source profile **only after** the evidence supports a precise HTTP contract; run focused tests, full applicable CI and source-specific fresh replay. Do not enable scheduler, allowlist, reconciliation, archival, GEO or publication.

## Acceptance and CI

Source contract: **NOT READY**. Terminal page: *historical UI only*. Distinct IDs: *22 previously observed on pages 2–3, not current complete proof*. Fresh individual details: **0 verified** in the 2026-11-09 GD sample; page-1 catalogue links: **20**. Live price/status success rate: **NOT MEASURED**. Production acceptance: **NO**.

Tests: `pytest -q tests/test_alfalot_audit.py` for standalone module; project-wide applicable CI verifies shared behaviour. No synthetic fixture is presented as live provider evidence.

**PRODUCTION ENABLED: NO**
