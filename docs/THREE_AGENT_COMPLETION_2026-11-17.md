# Three-agent continuation — 17 November 2026

## Scope recovered from the handoffs

1. Finish source/GEO operational acceptance after the merged audit fixes,
   including Sberbank-AST, Vertrades and M-ETS combined production passes.
2. Resolve reports 1–44 (41 unique lots), proving source facts, persistence and
   current map behavior rather than equating merged parser fixes with repaired data.
3. Complete M-ETS #1232: exact scoped identity on oversized multi-view HTML,
   late-procedure canaries, complete selected-catalogue evidence, CI and release.

## Implemented continuation

- #1232 now extracts a complete exact primary generalview from a bounded stream.
  Prefix retention remains 6 MB; the independent decompressed scan budget is 32 MB.
  Recommendations, incomplete views, identity contradictions and missing property
  evidence remain excluded. Late lot 215033-150 places its SKU beyond 6 MB.
- Exact lot views for 215033-1/93/150 retain approximately 67–69 KB and preserve
  the observed price and cadastre. The final sibling number 442 also fits the
  bounded extraction, with approximately 70 KB retained.
- Live pages 230–231 passed 40/40. Two records on the current page 228 lack
  cadastral evidence and remain rejected; this is not a price/identity waiver.
- TBankrot paused status, exact panel identity and enrichment version 4;
  Torgi.gov recursive lot-scoped cadastres and real UTC→Moscow conversion;
  Torgi Russia timeline/stages/status and enrichment version 6;
  BidExpert exact detail title, named lifecycle dates and procedure status.
- Shared address cleanup stops before legal disclosures. Property-backed
  receivables remain separate from property sales; retained occupancy rights
  alone do not turn an actual property sale into a lease transaction.
- Exact report repair has read-only preview, immutable identity validation,
  optimistic state checks, review protection and canonical isolation.
- User subsequently explicitly requested physical deletion of the incorrect
  reviewed cohort, including its ratings. The reviewed cohort contains 38 lots;
  the remaining 3 are to be corrected. The removal helper preserves unrelated
  source/canonical members and historical LotIssueReport rows, and requires a
  backup that passed isolated restoration before its operator script can apply.

## Evidence and remaining acceptance

Private row snapshots, HTML, exact IDs and before/after manifests are local
operator artifacts, excluded from Git. The report preview and M-ETS JSONL
are under `.artifacts/finish/` in the continuation worktree.

- The live selected catalogue changed from the historical 5063/254 to roughly
  5090 lots / 255 pages. Coverage must use the actual observed run and bounded
  drift checks, not a frozen old cardinality.
- Full M-ETS detail acceptance is running; its report must be reviewed to the
  terminal page, including every rejection code, before #1232 is released.
- TBankrot browser collection confirmed authentication but the account is
  restricted to a tariff below Business/Pro; private dates are absent from the
  returned HTML. Do not invent or erase historical dates from that absence.
- Sistematorg returned network timeouts from HTTP clients and the browser;
  current network/VPN conditions have not supplied fresh detail evidence.
- Production report resolution, current-map publication, combined source
  acceptance and a second idempotence pass must be recorded before closeout.

## Production report resolution and map verification

- The verified-restoration backup preceded the committed transaction: 38 reviewed
  erroneous lots and their source rows were physically removed; the remaining
  three lots were corrected and are closed/archived. Historical reports remain.
- The operator increased REG.RU S3 quota to 30 GB. The proposed orphan cleanup
  was not applied (zero orphan objects deleted). A 512 MiB multipart capacity
  probe succeeded and removed its own temporary object.
- The background publisher promoted `20261117T192402672354Z-r7-bundle-s3` with
  52,626 points. Public S3 manifest verification succeeded. All 41 report-scoped
  IDs are absent from current point tiles; exactly the three corrected archived
  rows remain in the database. Rebuilding again correctly skips unchanged input.
- Full M-ETS read-only traversal reached its terminal page at SHA `7ebc70db`:
  254 pages, 5,070 detail attempts, 5,018 accepted, 52 rejected (37 missing property
  evidence, 15 status mismatches). This is terminal traversal evidence, not a
  zero-rejection acceptance. Reject review and combined production acceptance
  remain open; reconciliation/archival was disabled throughout.
- M-ETS streamed-body retry and same-SHA checkpoint changes have been merged
  into the continuation branch. Release/deployment acceptance remains pending.
