# GEO root-cause remediation — 2026-11-10

Scope: generalized source-address and provider-chain fixes; no production data mutation in this PR.

## Evidence and root causes

1. **Ambiguous source address provenance.** Lot-online's generic `Адрес` control refers to an organizer/other party, while the property address is in the product title and full description. CDTRF's region-only badge can mask a full address inside the verified *individual* `lotInfo`. NISTP's numbered section may contain a short `Местонахождение имущества` and detailed per-asset address inside its own property-description field. Torgi Russia's title can contain an address after the cadastral identifier.
2. **Unreliable historical/geocoder candidate precedence.** Previously stored non-property addresses could be queried before a more precise asset address still present in `title` or `description`. Russian undotted abbreviations and apartment tails further reduced Photon matching.
3. **Provider configuration and classification.** The principal Compose worker did not forward `PHOTON_BASE_URL` even when defined in the host environment. A missing Photon URL returned a silent `None`, causing false semantic `no_coordinates` instead of operational retry. The running production Compose/environment has NOT been independently inspected; do not claim this was the reason for every historical miss.
4. **Data loss on shallow batch refresh.** The batch processed-lot projection used `address=lot.address` even for absent incoming addresses, unlike the single-lot path's preserve-existing behavior. A subsequent shallow catalogue hit could erase a verified detail address.
5. **Over-broad cached query identity and false precision.** Cache keys previously covered only the first address candidate; the new query candidate sequence and a version token are included. Photon no longer equates an unnumbered street/settlement feature to a numbered building.

## Implementation

- A shared asset-scoped evidence helper uses only the lot's own title/description and rejects contradictory cadastral regions and conflicting numbered addresses. External contact/organizer addresses are not trusted as property evidence.
- Reuse that helper in geocoding candidates and the numbered NISTP detail parser. Continue independent source-specific identity/price/status checks.
- Preserve old successful geocoding snapshots, fail closed on provider outages, keep public-provider mass fallback opt-in.
- Pass the optional local Photon endpoint to worker and API; never assume its existence and never silently treat its absence as a semantic no-match.
- Extract the pure GEO address builder into its own module without changing public import names.
- Preserve previously recovered property address in the batch projection when a later observation omits an address.

## Before any production release or recovery campaign

1. Finish existing 7-source and backup/restore safety gates, or explicitly coordinate a new release. Do **not** bypass them.
2. Check actual worker environment (without printing secrets): whether `PHOTON_BASE_URL` is configured; DNS/network reachability to its `/api` endpoint; and its real geocoding responses. The Compose file alone is not runtime evidence.
3. Measure deferred GEO backlog by source, reason and provider-attempt category and compare against actual raw fields on a representative stratified sample.
4. Run parser regression tests, full SQLite/PostgreSQL CI and one bounded geo canary of selected IDs. Verify house/street and cadastral-region identity of all accepted coordinates, not just improvement in hit rate.
5. Only then, under an operator-approved throttle and pause/lease controls, enqueue a limited cohort from `deferred_no_match`. **Do not bump** `_GEO_STRATEGY_VERSION` as a shortcut: its existing refresh path automatically requeues eligible historical failures for the new strategy version.
6. Verify DB→GeoSnapshot→MapDataset→S3→browser for accepted lots, compare wrong-pin rate and operational/semantic failure classifications, and scale in stages.

**Not claimed:** All historical `deferred_no_match` entries have been resolved, the actual production Photon instance is healthy, or full-source reconciliation has passed.
