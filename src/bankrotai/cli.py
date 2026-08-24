from __future__ import annotations

import argparse
import json
import sys
import os
from decimal import Decimal

from bankrotai.core import get_logger, get_settings
from bankrotai.db import init_db, session_scope, get_processed_lot, find_unappraised_lots
from bankrotai.domain import NormalizedLot
from bankrotai.logic import persist_lot
from bankrotai.scrapers import import_manual_html, GorodTorgiClient, TorgiGovClient, TorgiGovSearchFilters
from bankrotai.ai import OpenAIAppraiser, apply_evaluation_to_lot

logger = get_logger("cli")

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bankrotai")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("init-db", help="Initialize DB")
    desktop_p = subparsers.add_parser("run-desktop", help="Run GUI")
    desktop_p.add_argument("--smoke-test", action="store_true")
    api_p = subparsers.add_parser("run-api", help="Run API")
    api_p.add_argument("--host", default="0.0.0.0")
    api_p.add_argument("--port", type=int, default=8000)
    ingest_p = subparsers.add_parser("ingest-manual", help="Ingest HTML")
    ingest_p.add_argument("file")
    ingest_p.add_argument("--city", default="yaroslavl")
    eval_p = subparsers.add_parser("eval-lot", help="Eval lot")
    eval_p.add_argument("id", type=int)
    sync_p = subparsers.add_parser("sync-region", help="Sync region")
    sync_p.add_argument("region")
    sync_p.add_argument("--force", action="store_true")
    torgi_p = subparsers.add_parser("search-torgi-gov", help="Search online lots on torgi.gov.ru")
    torgi_p.add_argument("--search", default="")
    torgi_p.add_argument("--region", default="")
    torgi_p.add_argument("--category", default="")
    torgi_p.add_argument("--price-min", type=float, default=None)
    torgi_p.add_argument("--price-max", type=float, default=None)
    torgi_p.add_argument("--notice-status", default="")
    torgi_p.add_argument("--lot-status", default="")
    torgi_p.add_argument("--page", type=int, default=1)
    torgi_p.add_argument("--limit", type=int, default=20)
    torgi_p.add_argument("--all-pages", action="store_true")
    torgi_p.add_argument("--max-items", type=int, default=5000)
    torgi_p.add_argument("--show-params", action="store_true", help="Print raw torgi.gov.ru request params in meta")
    torgi_p.add_argument("--import-db", action="store_true", help="Persist found lots to the local database")
    subparsers.add_parser("appraise-all-pending", help="Appraise pending")
    subparsers.add_parser("test-parse", help="Test parse")
    test_p = subparsers.add_parser("test-html", help="Test parse saved TBankrot HTML")
    test_p.add_argument("file")
    backup_p = subparsers.add_parser("backup-db", help="Create and verify a consistent SQLite backup")
    backup_p.add_argument("--destination", default="backups")
    backup_p.add_argument("--retain", type=int, default=14)
    verify_p = subparsers.add_parser("verify-backup", help="Verify a SQLite backup without restoring it")
    verify_p.add_argument("file")
    restore_p = subparsers.add_parser("restore-db", help="Restore a verified SQLite backup")
    restore_p.add_argument("file")
    restore_p.add_argument("--confirm", action="store_true", help="Required safety confirmation")
    user_p = subparsers.add_parser("create-user", help="Create or rotate a web application user")
    user_p.add_argument("username")
    user_p.add_argument("--role", choices=("reader", "admin"), default="reader")
    user_p.add_argument("--password-env", default="AUTH_BOOTSTRAP_PASSWORD")
    read_sync_p = subparsers.add_parser("sync-read-model", help="Synchronize the public read-only data model")
    read_sync_p.add_argument("--max-items-per-source", type=int, default=5000)
    geo_p = subparsers.add_parser("geocode-pending", help="Geocode a bounded batch of stored lots")
    geo_p.add_argument("--limit", type=int, default=250)
    geo_p.add_argument("--re-geocode-existing", action="store_true")
    subparsers.add_parser("geocoding-stats", help="Show persisted geocoding quality statistics")
    repair_p = subparsers.add_parser("repair-map-read-model", help="Repair missing SourceLot map links")
    repair_p.add_argument("--limit", type=int, default=1000)
    bidexpert_repair_p = subparsers.add_parser("repair-bidexpert-addresses", help="Repair truncated BidExpert addresses")
    bidexpert_repair_p.add_argument("--limit", type=int, default=20_000)
    bidexpert_repair_p.add_argument("--apply", action="store_true")
    return parser

def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "init-db": init_db()
    elif args.command == "run-desktop":
        if args.smoke_test and "--smoke-test" not in sys.argv:
            sys.argv.append("--smoke-test")
        from bankrotai.gui import main as run_gui
        return run_gui()
    elif args.command == "run-api":
        from bankrotai.api import run_api; run_api(args.host, args.port)
    elif args.command == "ingest-manual":
        with session_scope() as s: import_manual_html(s, args.file, args.city)
    elif args.command == "sync-region":
        from bankrotai.tasks import sync_public_region_task
        sync_public_region_task(args.region, args.force)
    elif args.command == "search-torgi-gov":
        category_code = TorgiGovClient.CATEGORY_LABEL_TO_CODE.get(args.category.lower(), args.category) if args.category else None
        filters = TorgiGovSearchFilters(
            search_text=args.search,
            subject_rf=args.region or None,
            category_code=category_code,
            price_min=args.price_min,
            price_max=args.price_max,
            notice_status=args.notice_status or None,
            lot_status=args.lot_status or None,
            page=max(1, args.page),
            page_size=max(1, min(args.limit, 100)),
        )
        try:
            client = TorgiGovClient(diagnostics=args.show_params)
            if args.all_pages:
                filters.page = 1
                filters.page_size = 100
                lots, meta = client.search_all_lots(filters, max_items=args.max_items)
            else:
                lots, meta = client.search_lots(filters)
        except Exception as exc:
            print(f"Ошибка поиска torgi.gov.ru: {exc}", file=sys.stderr)
            return 2
        if args.import_db:
            init_db()
            with session_scope() as s:
                for lot in lots:
                    persist_lot(s, lot)
        print(json.dumps({
            "meta": meta,
            "items": [
                {
                    "external_id": lot.external_id,
                    "title": lot.title,
                    "category": lot.category,
                    "region": lot.region_name or lot.region_slug,
                    "price": lot.start_price or lot.current_price,
                    "status": lot.auction_status,
                    "url": lot.lot_url,
                }
                for lot in lots
            ],
        }, ensure_ascii=False, indent=2, default=str))
    elif args.command == "appraise-all-pending":
        init_db()
        with session_scope() as s:
            lots = find_unappraised_lots(s)
            appraiser = OpenAIAppraiser()
            for l in lots:
                # Full normalization for CLI using helper factory
                nl = NormalizedLot.from_processed_lot(l)
                apply_evaluation_to_lot(l, appraiser.evaluate(nl))
    elif args.command == "eval-lot":
        init_db()
        with session_scope() as s:
            l = get_processed_lot(s, args.id)
            if l:
                nl = NormalizedLot.from_processed_lot(l)
                apply_evaluation_to_lot(l, OpenAIAppraiser().evaluate(nl))
    elif args.command == "test-parse":
        print(f"Found {len(GorodTorgiClient('yaroslavl').fetch_lots())} lots")
    elif args.command == "test-html":
        from bankrotai.scrapers import ManualHtmlParser

        parser = ManualHtmlParser()
        lots = parser.parse_file(args.file)

        print(f"Найдено лотов: {len(lots)}")

        for lot in lots[:20]:
            print("-" * 80)
            print("ID:", lot.external_id)
            print("TITLE:", lot.title)
            print("PRICE:", lot.current_price, lot.price_text)
            print("STATUS:", lot.status)
            print("URL:", lot.url)
            print("AREA:", lot.area)
            print("LAND:", lot.land_area)
            print("CAD:", lot.cadastral_numbers)
            print("ADDRESS:", lot.address)
    elif args.command in {"backup-db", "verify-backup", "restore-db"}:
        from bankrotai.backups import create_sqlite_backup, restore_sqlite_backup, verify_sqlite_backup

        if args.command == "backup-db":
            result = create_sqlite_backup(args.destination, retain=max(1, args.retain), label="manual")
        elif args.command == "verify-backup":
            result = verify_sqlite_backup(args.file)
        else:
            if not args.confirm:
                parser.error("restore-db requires --confirm; a safety backup is created automatically")
            result = restore_sqlite_backup(args.file)
        print(json.dumps({
            "path": str(result.path),
            "integrity": result.integrity,
            "alembic_version": result.alembic_version,
            "processed_lots": result.processed_lots,
            "size_bytes": result.size_bytes,
        }, ensure_ascii=False, indent=2))
    elif args.command == "create-user":
        from bankrotai.auth import upsert_user

        password = os.getenv(args.password_env, "")
        if not password:
            parser.error(f"{args.password_env} is not configured")
        init_db()
        with session_scope() as session:
            user = upsert_user(session, args.username, password, role=args.role)
            print(json.dumps({"id": user.id, "username": user.username, "role": user.role}))
    elif args.command == "sync-read-model":
        from bankrotai.services.read_model_sync import sync_read_model

        init_db()
        counts = sync_read_model(
            session_scope,
            max_items_per_source=max(1, min(args.max_items_per_source, 20_000)),
        )
        print(json.dumps(counts, ensure_ascii=False, indent=2))
    elif args.command == "geocode-pending":
        from bankrotai.services.geo_backfill import geocode_pending_lots

        init_db()
        result = geocode_pending_lots(
            session_scope,
            limit=args.limit,
            re_geocode_existing=args.re_geocode_existing,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "geocoding-stats":
        from bankrotai.services.geo_backfill import geocoding_statistics

        init_db()
        with session_scope() as session:
            result = geocoding_statistics(session)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "repair-map-read-model":
        from bankrotai.services.read_model_repair import repair_missing_processed_links

        init_db()
        with session_scope() as session:
            result = repair_missing_processed_links(session, limit=args.limit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "repair-bidexpert-addresses":
        from bankrotai.services.bidexpert_address_repair import repair_bidexpert_addresses

        init_db()
        with session_scope() as session:
            result = repair_bidexpert_addresses(session, limit=args.limit, apply=args.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else: parser.print_help()

if __name__ == "__main__":
    sys.exit(main() or 0)
