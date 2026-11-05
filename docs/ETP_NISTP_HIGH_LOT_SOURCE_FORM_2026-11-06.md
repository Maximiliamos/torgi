# NISTP high-numbered lot evidence — read-only public form probe

## Grounded operator results (2026-11-06)

- 83 tests passed at SHA `c7669a5`.
- NISTP fresh sample: **9/10** verified. One additional failure is
  `nistp.ru:502379:259` on catalogue page 16.
- Independently checked trade `502379` publicly displays exactly 100
  numbered lot tables, with max table number 128. Previous failed
  individual lot numbers: 139, 179, 239, 339, 439, 539, plus new 259.
- CDTRF fresh sample: **10/10** individually verified, with two retained
  `source_title_disagreement_review_required` observations. This is a
  passing *bounded sample*, not full production acceptance.
- The overall two-source sample CLI exit code is 1 because NISTP 9/10.

## New experiment

The provider's official public NISTP homepage provides distinct
`trade_number` and `lot_number` form fields, a `view_type=lot` mode,
and active-trade status selections. The new probe reads the exact
trade code from the provider's shared trade heading (not inventing one),
then runs seven bounded **GET** searches by exact trade code and one
high-numbered lot at a time.

A search result must show the exact provider trade+lot identity, a
positive catalogue-listed price, recognized selected active source
status, checked active filters, matching input echoes and no extra
lot IDs before it can be classified as a **catalogue-only exact row**.

Even when all of those conditions hold:

- The source only proves its **catalogue** row, not the individual
  `table_lot_N` section or independent `detail` price.
- `independent_lot_detail_verified=false`, `source_accepted=false`,
  `production_database_writes=0`, `archival_allowed=false`.
- If the website ignores a form field, hides a selected checkbox, or
  returns unrelated lots, the exact search is NOT proven.
- Do **not** fill missing price, cadastral information or property
  rights from another numbered section.
- The 43 terminal source statuses inside the older active-state filter
  remain a separate blocker.

The companion PowerShell runner reads the already verified **CDTRF
10/10** JSON file `cdtrf-ru-20261105T233118Z.json`; it makes **no
fresh CDTRF requests** and never claims a new CDTRF 10/10 result.

## Operator command

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-nistp-high-lot-and-cdtrf-evidence-no-vpn.ps1
```

New NISTP evidence is saved as UTF-8
`nistp-high-lot-source-form-<UTC timestamp>.json`.
Do not turn the catalogue-only evidence into an individual detail
acceptance without a separate, source-proven field-level contract.
