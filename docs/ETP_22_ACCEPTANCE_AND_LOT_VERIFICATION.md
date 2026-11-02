# ETP 22: acceptance and official-lot verification

Updated 2026-11-02. This is an acceptance protocol, **not** a claim that
22 adapters are operational. See individual numbered ETP specifications.

## Operator stages

1. Freeze exact Git SHA and export verified PostgreSQL/S3 backups. Rehearse restore
   in a disposable environment, without mutating production.
2. For each source, capture catalogue with official filters, every page number,
   response SHA-256, stable IDs and evidence that the **last page** was reached.
   Record blocked/429/403/TLS as failures, never empty success.
3. Capture each individual lot detail, not just a procedure. Store official HTTPS
   URL, response SHA-256, source lot number, price and status. Never use a
   neighbouring lot's price or deposit.
4. Export matching source-scoped records from an **isolated staging** DB and
   the staging map. Never use production import as a test substitute.
5. Compare catalogue -> detail -> DB -> map using the read-only audit script;
   record every mismatch, rejected page and unverifiable geo evidence.
6. Have an independent operator inspect a sample of official live cards,
   including active/closed/public-offer statuses, multi-lot procedures,
   price changes and lots with missing location.
7. Only after manual source approval, populate the source acceptance allowlist
   and run a bounded write canary on staging. Confirm repeated updates are
   idempotent, map filters match source IDs, and archive counts remain stable.
8. Run exact-SHA P1/P11 and real production post-deploy monitoring only after
   review of the code, the staged canary, rollback and source-specific contracts.

## Read-only audit invocation

```powershell
python -m bankrotai.services.etp_22_acceptance_audit --input evidence.json --output report.json
```

The script exits 1 for any rejected or missing source. It **does not** connect
to the production database, scrape official sites, or prove authenticity of the
submitted captures. It checks the supplied independently collected evidence.
Until the last live-card comparison is done, leave sources unapproved.

Evidence JSON structure:
```json
{
  "sources": [
    {
      "source_id": "m-ets.ru",
      "total_pages": 1,
      "last_page_proven": true,
      "catalogue_pages": [
        {"number": 1, "snapshot_sha256": "64 lowercase hex characters", "lot_ids": ["m-ets.ru:12345-1"]}
      ],
      "detail_lots": [
        {"external_id": "m-ets.ru:12345-1", "official_url": "https://m-ets.ru/12345-1",
         "detail_sha256": "64 lowercase hex characters", "title": "Земельный участок",
         "start_price": "123000.00", "current_price": null,
         "auction_status": "active", "cadastral_number": "76:10:012345:123"}
      ],
      "database_lots": [
        {"external_id": "m-ets.ru:12345-1", "source_system": "m-ets.ru",
         "title": "Земельный участок", "start_price": "123000.00",
         "current_price": null, "auction_status": "active",
         "cadastral_number": "76:10:012345:123"}
      ],
      "map_lots": [
        {"external_id": "m-ets.ru:12345-1", "source_system": "m-ets.ru",
         "latitude": 57.63, "longitude": 39.89, "geo_evidence": "exact_cadastre"}
      ],
      "operator_review": {
        "live_official_cards_checked": true, "reviewed_by": "operator",
        "checked_at_utc": "2026-11-02T14:00:00Z"
      }
    }
  ]
}
```

Replace placeholder hashes with SHA-256 of the captured official response
bytes. For data with missing verified coordinates, exclude the map marker;
the report will warn but not invent approximate coordinates.

## Production write safety

`etp_all_sources_campaign.py` requires the existing operator backup/write
flags **and** `ETP_ACCEPTED_SOURCE_IDS` to cover every runnable source.
This is a **fail-closed compatibility gate**; populating the variable alone
does **not** validate the source. With any missing approval, the campaign
refuses to write. This intentionally disables the previous indiscriminate
all-source import until review. Source-specific bounded canary paths must be
reviewed separately. Do not enable 16 sources to bypass the guard.

## Known remaining deficits

- Detail-level and procedure/lot identity still require source-specific fixes
  for all non-accepted adapters.
- Sistematorg needs independent per-lot section parsing and historical
  migration plan, not just null-price protection (PR #1061).
- M-ETS generic mass-import route differs from dedicated MetsConnector.
- Five providers lack verified public source access; do not circumvent blocks.
- Full-catalogue completeness, schedule freshness, 5-source health,
  price/status updates and real GEO/map publication remain unverified.
