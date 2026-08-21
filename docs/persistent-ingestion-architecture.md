# Persistent nationwide lot ingestion

The implementation extends the existing operating model instead of creating a
parallel catalogue.

- Source adapters reuse `AuctionConnector`, `NormalizedLot`, and the existing
  GIS Torgi, TBankrot, and LOT-ONLINE clients.
- Every publication is upserted into `SourceLot` by
  `(source_system, external_id)` and linked through the existing canonical and
  processed-lot model.
- A PostgreSQL-backed sync run and lease coordinate full refreshes. Missing
  publications are archived only after a complete successful source scan;
  partial/failed scans never mass-archive that source.
- The map remains a read-only projection of persisted `ProcessedLot` and
  `LotGeoSnapshot` rows. Login and map reload do not call auction sources.
- Region codes use one canonical directory. Source-specific region identifiers
  remain separate adapter mappings.
- Existing geocoding, duplicate reconciliation, ETag, viewport API, task state,
  authentication, and reliability controls remain in place.

The production backfill is staged by region and is not started while the
current origin-to-Neon availability gate is failing.
