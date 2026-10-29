# ETP source implementation and browser capture handover — 2026-10-29

## Repository artifacts

- Browser collector **v6** source: `scripts/etp_capture_v6.py` and Windows launcher `scripts/RUN_ETP_CAPTURE_V6_WINDOWS.bat`. This is a repository-native successor to the v5 ZIP from the conversation. It runs in read-only mode with sequential Playwright Chromium, bounded navigation, screenshots, JSON XHR bodies and per-source events. It does not bypass CAPTCHA, anti-DDoS or HTTP 429, and does not touch PostgreSQL, Celery or S3.
- Local replay validator: `scripts/verify_etp_capture.py` backed by `src/bankrotai/connectors/registry/etp_capture_replay.py`. This checks archive structure, independent first/second page identities and source-safe URLs, and emits only summary diagnostics.
- Saved capture metadata: `docs/etp-capture/2026-10-29-v5-redacted-summary.json`. It contains only counts and status classifications for 22 providers, with no raw HTML, screenshots, API responses, person names, cookies, authentication headers or access tokens.
- Original v5 collector ZIP SHA-256: `324a75f5ba9a4b24daef18325d3efeeec52faf4b917355356864cccec2f0c647`. Original v5 capture ZIP SHA-256: `431f9e7f1f39b95d10f083b7999d70b4d6ba0a44cafab9e5742bc7a47adcec10`. Hashes identify the original local files, **not** files available for download from this public repository. Preserve them privately until redacted and approved.

## Pilot source matrix

| Source group | Platforms | Current safe capability |
| --- | --- | --- |
| Previously present production source | Lot-online/РАД | Existing adapter; no duplicate collection |
| Best detail evidence | M-ETS, CDTRF | Existing read-only detail parsers; CDTRF API evidence parser and source-ID validation |
| Browser-backed candidates, requiring detail contracts | NISTP, Arbbitlot, ETPU, Sistematorg, Centerr, Aukcioncenter, Rus-on, ZakazRF, UTPL | Bounded HTML discovery + captured-page replay, not archive-safe ingestion |
| Partially accessible | Sberbank-AST, Roseltorg, Fabrikant, Alfalot, Vertrades | Filtered Sberbank bankruptcy API parser and/or observed HTML; incomplete detail or pagination |
| Blocked/unconfirmed | RTS-tender, Profit, Ausib, uTender, Electro-torgi | Explicit challenges, TLS/rate limit or catalogue routing limitations |

The last v5 capture saved 47 apparent detail pages, 234 textual network bodies and 121 screenshots. This does **not** mean 47 verified real-estate lots or 234 usable APIs. For 16 providers the collector reported catalogue + details and for one catalogue-only. Two sites produced navigation errors, two no verified catalogue, and one HTTP 429 access-denied.

## Usage (Windows)

Install Python 3.11+, then run in a checkout of the ETP development branch:

```powershell
python -m pip install playwright requests beautifulsoup4
python -m playwright install chromium
.\scripts\RUN_ETP_CAPTURE_V6_WINDOWS.bat --sites m-ets.ru cdtrf.ru nistp.ru
python scripts/verify_etp_capture.py --input "etp-evidence\TORGI_ETP_V6_....zip" --output etp-audit-redacted.json
```

Run without `--sites` for 22 profiles. Do not automatically commit the resulting raw ZIP; inspect HTML and API bodies for personal data, confidential application details, and other sensitive material.

## Explicit outstanding acceptance gates

1. Prove every source's relevant property-catalogue page traversal, with deterministic cursor replay, page count and no missing pages.
2. Verify a stable `(source_system, external_id)` for **each actual lot**, not only the containing procedure or debtor.
3. Extract and verify sale type, active status, initial/current prices and dates from actual detail, including public-offer price schedules.
4. Exclude claims, vehicles, leases, fractions and other non-target property; test mixed multi-lot procedures.
5. Test deduplication against GIS Torgi and Lot-online using notice/lot and cadastral identity before map publication.
6. Validate archival safeguards, status history/journal, geocoding, current coordinates, API cache, filtered S3 map and rollback in isolated PostgreSQL.
7. Require current-commit green CI and staged deployment gates home → REG.RU → Cloudflare; verify on sterdez.online before declaring live.

**Not approved**: modifying default source sync, auto-archiving, pushing the raw snapshots into public GitHub, or claiming 100% until the acceptance gates are complete.
