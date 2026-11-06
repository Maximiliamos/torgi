# Focused structural evidence after 17 failed cards

Historical operator evidence at SHA `14b160d8`:

- NISTP: 10/10 failed again, 6 missing requested numbered sections in
  trade `502379` (lot numbers 139,179,239,339,439,539);
  4 `incomplete_numbered_lot_section` in other trades.
- CDTRF: 7/7 `trade_identity_or_title_mismatch`, cannot safely distinguish
  HTML code, Nuxt trade ID, Nuxt lot ID and HTML-vs-SSR title yet.
- 18 targeted tests passed before the 17-page operator run.

## Evidence-only next step

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-etp-failed-card-structure-no-vpn.ps1
```

This command does NOT repeat 142+64 full catalogue pages and does not write
lot data. It validates the previous JSON and requests at most 5 shared NISTP
procedure pages and 7 CDTRF catalogue pages with their exact detail cards.
It does not store source HTML, original titles, descriptions, actual prices,
debtor names or addresses.

For NISTP, report shows individual table presence and range, whether exact
description fields exist in each targeted table, and how many other table
sections lack the expected schema. This helps distinguish truncated
procedure rendering from a parser failing on an unrelated defective lot.

For CDTRF, report checks separately whether the HTML trade number, Nuxt
trade number and Nuxt lot ID match the API pair, whether the visible title
matches Nuxt `lot.name`, and whether priceBegin equals the HTML sidebar
price. Do not relax identity checks until the booleans prove whether only a
title mismatch exists. A mismatched `tradeLotId` is a hard block.

The resulting `failed-card-structure-*.json` is read-only evidence.
The source status remains unaccepted and import/reconciliation/archival/
geocoding/map writes remain prohibited.
