# AukcionCenter source contract — partial read-only audit (2026-11-09)

**Source:** https://aukcioncenter.ru/etp/trade/list.html?show=current  
**Base main SHA:** `5997d239a71e253b7dd3386820ca08002f63f071`  
**Acceptance:** BLOCKED — documentation-only partial evidence, not parser acceptance.  
**PRODUCTION ENABLED: NO**

## Previously reported state
From `docs/ETP_PARSER_STAGE_CHECKLIST.md`: catalogue PASS (30 candidates), page 2 PASS, individual detail PASS, price FAIL, status FAIL, classification FAIL; last page NOT_TESTED.

## Official public observations and limitations

- The official bankruptcy trade listing exposes `page` and `show=current` URL parameters. Example page 2: https://aukcioncenter.ru/etp/trade/list.html?page=2&show=current . Do **not** infer any unverified filter semantics from `show=current`; the listing includes ended and cancelled procedures.
- An official response dated 2026-11-09 04:49 MSK for https://aukcioncenter.ru/etp/trade/list.html?page=497&show=current links onward to page 498.
- Page 498, official response dated 2026-11-09 04:49 MSK: https://aukcioncenter.ru/etp/trade/list.html?page=498&show=current . It displays page 498 as its last pagination number (15 listed procedure rows in the rendered page). **The page-498 visual last-page marker is observed, but full traversal, page 499 response and terminal invariants are NOT verified.**
- Page 2 was also viewed but the returned page snapshot had a 2026-08-22 clock. This must not be represented as a fresh 2026-11-09 live proof.
- The displayed row identifier is a procedure/trade number, e.g. `7496-ОКОФ`, whose listing contains distinct lots №1 and №2. Page 2 procedure `7416-ОАОФ` combines an automobile lot and land lot. Consequently row/procedure identity is **not** sufficient to identify an individual lot.
- The catalogue mixes realty, land, vehicles, equipment, corporate equity, receivables, lease rights and mixed asset bundles. A procedure-level keyword filter is unsafe.
- Labels `идёт приём заявок`, `торги завершены`, and `торги отменены` are visible in public catalogue snapshots. They are displayed at the procedure-row level. Per-lot status and date/status interactions remain unverified.
- The site's official regulations describe separate application periods and descending initial prices for public-offer sales. No initial/current price may be fabricated from deposits, bid steps or procedure-level text.

## Evidence counts (only observations actually made)
- Rendered listing pages inspected: 1 (cached 19 Oct), 2 (cached 22 Aug), 497 and 498 (9 Nov). Fresh 9 Nov rendered pages: **2**.
- Distinct item IDs enumerated over a complete catalogue: **NOT TESTED**.
- Full catalogue passes / digest comparison: **0 / NOT TESTED**.
- Individual detail cards opened and independently verified this audit: **0**.
- Individual lot ID binding: **NOT PROVEN**.
- Per-lot initial price, public-offer current price, status success rates: **NOT MEASURED**.
- Explicitly verified property classifier, dates, addresses, cadastral numbers, areas per individual lot: **NOT PROVEN**.

## Root-cause hypothesis (NOT verified code diagnosis)
The HTML listing renders rows corresponding to procedures with possibly multiple heterogeneous lots, and procedure-row status. Misinterpreting those rows as property lots or reusing their fields could misassign identity, price or status. This is a hypothesis, not a demonstrated parser defect; no code change is justified yet.

## Required next steps before implementation and review
1. Locate the actual existing AukcionCenter adapter/profile, its tests, and any previous evidence; assess original root cause in code.
2. Capture sanitized HTTP/HTML evidence for the official per-lot detail links/IDs and verify cross-page consistency.
3. Perform two bounded, rate-limited, **read-only** catalogue traversals through the terminal condition; compare IDs and content digests. Check page 499 to substantiate page 498 as a terminal boundary.
4. Verify >=30 individual distinct lot details, including multi-lot and mixed-property procedures, public offers, and ended/cancelled states. Capture initial price separately from current-price periods, deposits and auction steps.
5. Use source-specific fixtures and regression tests before changing the parser; preserve fail-closed behavior.
6. Run focused and full applicable CI. Do not modify production scheduling, allowlists, reconciliation, archival, `docs/ROADMAP.md` or the shared stage checklist.

## Files changed
- `docs/ETP_AUKCIONCENTER_AUDIT_2026-11-09.md` only.

## Tests
Not run: documentation-only checkpoint. No parser implementation is claimed.

**PRODUCTION ENABLED: NO**
