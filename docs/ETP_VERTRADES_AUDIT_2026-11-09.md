# Vertrades source-contract audit — 2026-11-09

**Assigned source:** vertrades.ru / official *arrested-property* catalogue on `arest.vertrades.ru`.

**Base SHA:** `34baeaf7202b190e83c1aeaf08ec46f2efa2da76`.

**PRODUCTION ENABLED: NO.** No scheduling, reconciliation, archival, allowlist or source maturity changes are made.

## Earlier state and root cause

Previous evidence reported catalogue PASS (40 candidates), page 2 PASS, individual detail BLOCKED, price FAIL and status FAIL. These figures are **historical reported evidence**, not a repeated 2026-11-09 live 40-card sample.

The source profile in `src/bankrotai/connectors/etp_sources/profiles.py` targets `https://arest.vertrades.ru/bidding` and procedure detail `https://arest.vertrades.ru/showTrades?id={identity}`. The per-lot extractor in `registry/direct_etp_catalogue.py` constructs a composite trade:lot catalogue identity. However, `etp_sources/common.py` deliberately raises `SourceAccessError` for every Vertrades `fetch_lot`, because it cannot isolate lots on a shared procedure page. This is the **code-level root cause of detail BLOCKED**; it does *not* prove that public detail is behind authentication.

## Public official page evidence (read-only)

- Arrested-property catalogue: https://arest.vertrades.ru/bidding ; query variant previously reported: `?bidding_type=1`. The filter's real POST/GET contract, source total and terminal page are **not newly proved**.
- Public example shared procedure: https://arest.vertrades.ru/showTrades?id=102288306 — public indexed content contains individually numbered sections `Сведения по лоту №1`, `№2`, `№3`, `№4`, `№5`, `№6`, `№7`, `№9`, `№10`, `№11`; these sections illustrate multiple lots in one procedure, and non-contiguous numbers.
- At that example URL, `Статус торгов по лоту` and `Начальная цена продажи имущества` occur inside individual sections. The fifth section shows `Торги по лоту отменены`, while others have `Торги завершены`. Distinct initial prices exist for different lots. These observations demonstrate why a global procedure-wide price/status would be unsafe.
- The site has different subdomains: `arest.vertrades.ru` for arrested property and `bankrot.vertrades.ru` for bankruptcy. A publicly indexed bankruptcy procedure `https://bankrot.vertrades.ru/showTrades?id=700878418` is **not evidence of the arrest subdomain's contract** and must not be mixed into this parser.
- Evidence limitations: inspection used publicly indexed HTML/text views rather than a network HAR/live browser session. The example was historical (2022), not an active 2026 lot. Its availability and markup were not independently re-fetched with a current raw HTTP capture.

### Verification matrix

| Check | Result |
| --- | --- |
| Catalogue path | Existing profile identified; fresh live contract unverified |
| Catalogue page 1/page 2/terminal | Historical page 2 reported PASS; **0 pages** revalidated with raw 2026-11-09 request evidence; terminal NOT proved |
| Two complete catalogue passes | NOT performed |
| Unique active catalogue IDs | Historical sample 40 candidates only; fresh number NOT measured |
| Individual detail mechanism | Public shared `showTrades?id=procedure_id` page observed; isolated subsections by numbered lot |
| Independent fresh individual detail samples | **0 of required 30** |
| Per-lot price/status | Explicit section labels observed on one historical shared procedure page; 2026 success rate NOT measured |
| Stable provider procedure ID | Detail `id` query parameter observed; durability unverified |
| Stable per-lot ID | Numbered sections observed, but cross-page/catalogue identity NOT verified |
| Property type, address, cadastral, area | Embedded in individual-lot text on example page; structured coverage NOT verified |
| POST/XHR/JS routes, filter, pageSize/last page | NOT proved |
| Source completeness, active-only filter | NOT proved |

## Changes in this branch

1. `src/bankrotai/connectors/etp_sources/vertrades_detail_audit.py`: **read-only, standalone** bounded section inspector. Extracts only numbered-lot, explicit initial-price, explicit status evidence; missing and duplicate labels fail closed (null/exception). No personal data or full page text is returned.
2. `tests/test_etp_vertrades_detail_audit.py`: synthetic, sanitized tests for multi-lot isolation, missing price, duplicate headings, duplicate prices and page size.
3. This source-specific report.

**Deliberately unchanged:** existing `fetch_lot` guard and capability; production connectors, shared roadmap and checklist, scheduler, reconciliation and archival.

## Remaining required work before enabling detail

1. Read-only raw HTTP capture on the *arrest* subdomain (not bankruptcy): catalogue GET/POST filter, page 1, page 2, verified terminal page, ideally two complete passes + ID digest.
2. At least 30 *currently available* distinct lot sections from representative procedures; verify catalogue composite ID maps to exact detail section number, status and price; distinguish cancelled/finished/active.
3. Confirm title, relevant property category, dates, address, cadastral and area extraction with explicit source labels and mixed-asset negatives.
4. Verify multi-lot forms, zero- or missing-price cases, price reductions/public offer and whether detail sections are collapsed through JS/XHR.
5. Validate tests in source repository environment and full applicable CI. No test run or green CI is claimed in this audit.
6. Only after these checks implement and review a source-specific `fetch_lot` adapter. Keep the existing fail-closed guard until then.

## Acceptance

**NOT PRODUCTION ACCEPTED.** Current change is strictly a source-specific contract research aid. No login, CAPTCHA bypass, anti-bot circumvention or mutation was attempted.

PRODUCTION ENABLED: NO
