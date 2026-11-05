# ETP stage checklist — operator contract

The bounded `etp_parser_checklist` module provides a **live read-only,
stage-by-stage diagnostic for all 22 registered target sources**, including
the existing Lot-online connector. It never imports, reconciles, geocodes,
archives or publishes data. Its purpose is explaining why an ETP adapter
stops, not certifying a complete source scan.

## Run on trusted Home ingestion worker

```powershell
docker exec bankrotai-home-ingestion-worker python -m bankrotai.services.etp_parser_checklist --all --pages 2 --detail-probes 1
docker exec bankrotai-home-ingestion-worker python -m bankrotai.services.etp_parser_checklist --source nistp.ru --pages 2
```

Scheduled Home read-only workflow: `ETP parser stage checklist (Home read-only)`.
Download its `etp-parser-stage-checklist` artifact for the exact job SHA
and read `etp-parser-stage-checklist.json`. Its other artifact file
preserves the source-by-source progress log. A successful workflow means
diagnostics ran and an artifact was retained; it **does not** mean 22
parsers were accepted or imported.

## Stages

The machine-readable stages cover the complete acceptance chain:

1. `catalogue_url`, `dns_resolution`, `tls_certificate`, `http_response`,
   `redirect_chain`, `transport`;
2. `catalogue_markup`;
3. `browser_javascript`, `cookie_or_tutorial_modal`, `catalogue_filter_state`;
4. `lot_discovery`, `procedure_lot_separation`;
5. `stable_lot_ids`, `lot_id_provenance`;
6. `pagination_next`, `pagination_page_contract`, `last_page_proven`,
   `two_pass_catalogue_match`;
7. `individual_detail`, `detail_identity`;
8. price/status/address/cadastre/area/date stages;
9. catalogue/detail consistency stages for those fields;
10. `postgres_backup_restore`, `production_import`, `postgres_insert`,
    `postgres_update`, `postgres_deduplication`, `mass_archival_guard`;
11. `geocoding`, `map_dataset_build`, `map_publication`, `s3_publication`,
    `website_visibility`, `website_lot_links`, `website_source_filter`;
12. `scheduler_09_msk`, `scheduler_21_msk`,
    `scheduler_two_consecutive_cycles` (plus the legacy aggregate `scheduler`).

The bounded HTTP diagnostic can only populate the stages it actually observes.
The remaining stages must stay `NOT_TESTED`; their presence in the schema is not
evidence that PostgreSQL, GEO, S3, the public website or either schedule ran.

A stage reports `PASS`, `FAIL`, `BLOCKED`, `NOT_TESTED`, or
`N/A`. `first_failure` identifies the earliest observed exception
category and page, where available. Reports retain a sanitized HTTPS
endpoint (scheme, host and path) plus query parameter names, HTTP status
and exception class; query values, HTML, raw exception text, account
identities and individual lot data are not logged. Browser cookie/tutorial popups do not apply to HTTP-only
connectors; real browser adapters must assess them independently.

The single-page or two-page sample cannot establish:
- true terminal catalogue page or completeness without independent evidence;
- verified auction identity/price/status correctness for *all* listings;
- correctness of production database imports and map publication;
- freshness or correctness of automatic 09:00 / 21:00 Moscow schedules.

These remain `NOT_TESTED` until the full source-specific acceptance
workflow and downstream database/GEO/map validation pass. Never change
production allowlists based on this diagnostic alone.

## Torgi Russia

The full nationwide source deliberately requires a proven terminal page,
with `reconcile_missing=False` to avoid historical destructive archival.
The one-page FAST source retry is **not a full scan**: it must set
`require_source_end=False`, retain `max_batches=1`, and never archive.
A passing read-only source probe plus queued FAST task is not evidence
that the follow-on Celery sync completed. Inspect its durable source-run
result before declaring recovery.

## Safeguards

- Capture unique lot IDs and count in aggregates only.
- Do not defeat CAPTCHA, change TLS verification or access hidden APIs.
- Do not use a successful page-1 response as a production readiness claim.
- Never automatically import an unaccepted source.
- Separate retryable access failures from HTML schema failures.
