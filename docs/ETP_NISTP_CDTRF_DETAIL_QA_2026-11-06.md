# NISTP and CDTRF — bounded independent lot-detail QA

**Scope:** This is a **read-only sample**, not a replacement for the two full
catalogue walks and **not a source acceptance, production import, or release
approval**. No DB/GEO/map/archive/reconciliation operation is called.

## Grounded preceding proof (operator output, 5 Nov 2026)

- NISTP: 142 catalogue pages, 2,716 distinct lot IDs; both complete passes
  have identical ID and record hashes. Selected active filters were visibly
  checked on every page, yet 43 rows had terminal statuses (40 completed,
  2 failed, 1 cancelled). As a result the effective active source filter has
  **not** passed acceptance.
- CDTRF: 64 pages and 6,351 distinct IDs per pass in the latest clean pair;
  ID digest matches, no cross-page overlap or observed total-count drift.
  This is proof for the selected public catalogue partition only; **no
  independent lot detail was checked by that full-page scanner**.

## Next bounded validation

From the isolated worktree containing this branch:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-etp-nistp-cdtrf-detail-qa-no-vpn.ps1
```

The driver selects 30 deterministically spaced pages for each source and
uses a bounded reserve of extra pages if one contains no eligible sample.
It checks one provider-specific per-lot detail for each selected page:

- NISTP: compares the individual composite ID to the numbered lot section,
  confirms the independently parsed initial price, the source status mapped
  consistently, and property classification. Only the three intended active
  provider statuses are sampled. Terminal rows are neither promoted nor
  silently considered source acceptance.
- CDTRF: compares `tradeId` and `tradeLotId` from the official JSON catalogue
  against verified independent Nuxt lot detail, initial price provenance,
  normalized selected-trade status, and property classification. API
  `actualPrice` is **not** automatically treated as the same monetary kind as
  the detail's `priceBegin`.

The script writes UTF-8 JSON to
`D:\BankrotAI\etp-evidence\two-source-detail-qa`. It only writes source
IDs, counts, safe problem codes and booleans about field presence. It does
not store raw provider HTML, titles, addresses or debtor data.

## Guardrails

- Source-pair read-only detail QA != two full independent inventories.
- A successful 30/30 sample does not prove untested source records, historical
  status transitions, field-level legal ownership, geocoding or map publication.
- The NISTP active-filter contamination is still a separate P1 blocker.
- A source detail may legitimately have a different status than an earlier
  catalogue snapshot. This is captured as a mismatch for investigation,
  not silently declared a parser bug.
- Both reports always set `source_accepted=false`, `archival_allowed=false`,
  `production_database_writes=0`.
- A failed sample returns a nonzero exit code but still reports the other
  source. Do not disable safeguards to get a PASS.
