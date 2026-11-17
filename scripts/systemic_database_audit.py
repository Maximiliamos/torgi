"""Full database baseline: read-only repeatable snapshot, no corrective writes.

Each detector produces candidates, not a verdict about external source truth.
Output contains complete candidate IDs, never only a sample of the database.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import text

from bankrotai.db import SessionLocal


def main():
    with SessionLocal() as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        session.execute(text("SET LOCAL statement_timeout = '120s'"))

        def rows(sql):
            return [dict(row) for row in session.execute(text(sql)).mappings()]

        result = {
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "database_writes": 0,
            "scope": "all_processed_source_and_canonical_rows_including_archived",
            "candidate_semantics": "requires_validation_not_automatic_error_or_delete",
            "totals": rows("""SELECT
                (SELECT count(*) FROM processed_lots) processed,
                (SELECT count(*) FROM source_lots) sources,
                (SELECT count(*) FROM canonical_lots) canonicals""")[0],
            "processed_by_source": rows("""SELECT source_system, count(*) total,
                count(*) FILTER (WHERE NOT is_archived) unarchived,
                count(*) FILTER (WHERE review_status IS NOT NULL) reviewed,
                count(*) FILTER (WHERE needs_geo_check) needs_geo,
                count(*) FILTER (WHERE current_geo_lat IS NOT NULL AND current_geo_lon IS NOT NULL) with_geo,
                count(*) FILTER (WHERE nullif(trim(address),'') IS NULL) missing_address,
                count(*) FILTER (WHERE nullif(trim(cadastral_number),'') IS NULL) missing_cadastre,
                count(*) FILTER (WHERE start_price IS NULL) missing_start_price
                FROM processed_lots GROUP BY source_system ORDER BY source_system"""),
            "status_distribution": rows("""SELECT source_system, auction_status,
                is_archived, count(*) count FROM processed_lots
                GROUP BY source_system, auction_status, is_archived
                ORDER BY source_system, auction_status, is_archived"""),
            "source_dates": rows("""SELECT source_system, count(*) total,
                count(*) FILTER (WHERE application_start_at IS NULL) missing_application_start,
                count(*) FILTER (WHERE application_deadline IS NULL) missing_application_end,
                count(*) FILTER (WHERE auction_at IS NULL) missing_auction_at,
                count(*) FILTER (WHERE auction_timezone IS DISTINCT FROM 'Europe/Moscow') other_or_missing_timezone
                FROM source_lots GROUP BY source_system ORDER BY source_system"""),
        }
        conditions = {
            "missing_source_projection_link": "s.id IS NULL",
            "source_projection_identity_disagreement": "s.id IS NOT NULL AND (p.external_id IS DISTINCT FROM s.external_id OR p.source_system IS DISTINCT FROM s.source_system)",
            "source_projection_start_price_disagreement": "s.id IS NOT NULL AND p.start_price IS DISTINCT FROM s.start_price",
            "source_projection_address_disagreement": "s.id IS NOT NULL AND p.address IS DISTINCT FROM s.address",
            "source_projection_cadastre_disagreement": "s.id IS NOT NULL AND p.cadastral_number IS DISTINCT FROM s.cadastral_number",
            "closed_but_unarchived": "p.auction_status = 'closed' AND NOT p.is_archived",
            "negative_price": "p.start_price < 0 OR p.current_price < 0",
            "start_price_equals_deposit": "p.start_price > 0 AND s.deposit_amount > 0 AND abs(p.start_price-s.deposit_amount)<0.01",
            "application_dates_reversed": "s.application_start_at > s.application_deadline",
            "active_with_past_auction_date": "p.auction_status='active' AND NOT p.is_archived AND s.auction_at < (CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Moscow')",
            "cadastre_region_disagreement": "p.cadastral_number ~ '^[0-9]{2}:' AND p.region_code IS NOT NULL AND left(p.cadastral_number,2) <> p.region_code",
            "partial_or_invalid_coordinates": "(p.current_geo_lat IS NULL) <> (p.current_geo_lon IS NULL) OR p.current_geo_lat NOT BETWEEN -90 AND 90 OR p.current_geo_lon NOT BETWEEN -180 AND 180",
            "geo_present_pending_input_check": "p.needs_geo_check AND p.current_geo_lat IS NOT NULL",
            "nonproperty_category_unarchived": "NOT p.is_archived AND p.category IN ('car','vehicle','transport','equipment','receivables','debt','claim','movable')",
            "lease_title_unarchived": "NOT p.is_archived AND p.title ~* '^\\s*(аренда|субаренда|право заключения договора аренды)'",
            "legal_disclosure_in_address": "p.address ~* '(обременение|находится в залоге|договор займа|право требования)'",
        }
        candidates = {}
        for name, condition in conditions.items():
            candidates[name] = rows(f"""SELECT p.source_system,
                count(*) count, count(*) FILTER (WHERE NOT p.is_archived) unarchived,
                count(*) FILTER (WHERE p.review_status IS NOT NULL) reviewed,
                array_agg(p.id ORDER BY p.id) lot_ids
                FROM processed_lots p LEFT JOIN source_lots s ON s.processed_lot_id=p.id
                WHERE {condition} GROUP BY p.source_system ORDER BY p.source_system""")
        result["candidates"] = candidates
        result["source_without_projection"] = rows("""SELECT source_system, count(*) count
            FROM source_lots WHERE processed_lot_id IS NULL GROUP BY source_system""")
        result["source_without_canonical"] = rows("""SELECT s.source_system, count(*) count
            FROM source_lots s LEFT JOIN canonical_lots c ON c.id=s.canonical_lot_id
            WHERE c.id IS NULL GROUP BY s.source_system""")
        result["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        print(json.dumps(result, ensure_ascii=True, default=str))
        session.rollback()


if __name__ == "__main__":
    main()
