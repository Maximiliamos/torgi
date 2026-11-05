# NISTP + CDTRF: evidence-backed exact-detail fixes (2026-11-06)

## Source evidence (operator, no-VPN, SHA 62a9eb6)

- NISTP shared trade `502379`: the public card exposes 100 numbered
  tables, from 1 to 128. It does **not** expose requested lots
  139/179/239/339/439/539. Those six lots remain **UNVERIFIED**; there is
  no confirmed alternate official detail endpoint yet.
- NISTP `501804:1`, `501002:1`, `496865:1`: requested numbered table
  exists, but prior `tbody > tr` extraction sees zero labelled fields.
  A second independently testable parsing route now supports direct `tr`
  children, still strictly requiring per-lot labels, values, price, status
  and heading. Whether it resolves these *live* three needs operator evidence.
- NISTP `494080:3`: its own table has all required field labels, while
  six of thirteen sibling tables lack the required description field. The
  prior whole-trade parser rejected the entire page. Exact-lot mode now
  reads only target field values but still checks global duplicate section
  numbers and native identities. The full-trade parser stays strict.
- CDTRF `390579` and `389553`: HTML trade code, Nuxt trade ID, Nuxt
  tradeLotId and visible vs Nuxt initial price all match; only HTML H1 vs
  `lot.name` differ. Parser now chooses the specific `lot.name`, retains
  `source_title_disagreement_review_required` and does NOT skip ID or
  initial price guards. `ready_for_ingestion=false` for raw detail.
- CDTRF five other source lot IDs were not on their former live catalogue
  pages on the next run. This does not establish a parser defect or justify
  substitute-lot validation.

## Safe validation

1. Operator checks out exact HEAD of this branch in isolated clean worktree.
2. Run offline tests:
   `tests/test_nistp_detail_sections.py`,
   `tests/test_nistp_detail_adapter.py`,
   `tests/test_cdtrf_direct_html.py`,
   `tests/test_etp_cdtrf_canary.py`,
   `tests/test_etp_two_source_detail_qa.py`.
3. Run existing PS wrapper
   `scripts/run-etp-nistp-cdtrf-detail-qa-no-vpn.ps1 -RecheckFailedOnly`
   with explicitly pinned `20261105T222934Z` per-source reports.
4. The run revisits only the previously failing catalogue pages and matches
   **the original external ID** on each, not another candidate occupying
   the same position after drift.
5. A disappeared source ID on an old page yields
   `pinned_lot_not_in_active_catalogue_page`, not a false detail PASS.
   Check source IDs and exact review flags before considering acceptance.

### Non-negotiable gates

These code fixes do not prove live 17/17, the entire catalogue, title
provenance on untested lots, or the 43 terminal statuses leaking into the
NISTP active-filtered search. Do not merge/deploy as production acceptance
without independently observed test and live samples.

All updated QA reports remain `source_accepted=false`,
`archival_allowed=false`, `production_database_writes=0`,
`map_publication=false`. No automatic reconciliation/archival is permitted.
