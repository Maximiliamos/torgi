"""Read-only exact-code M-ETS acceptance. JSONL evidence survives interruption."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
from datetime import datetime, timezone

from bankrotai.connectors.registry.mets_public import MetsPublicPilot
from bankrotai.connectors.registry.mets_sync import MetsSourceCrawler, MetsSyncFilters


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", help="Comma-separated canary pages; omit for full walk")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    filters = MetsSyncFilters()
    counts = Counter()
    rejections = Counter()
    identities = set()

    def emit(value):
        value.update(at_utc=datetime.now(timezone.utc).isoformat(), head=sha,
                     database_writes=0, reconcile_missing=False, safe_for_archival=False)
        line = json.dumps(value, ensure_ascii=True, default=str)
        with output.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        print(line, flush=True)

    emit({"phase": "start", "pages": args.pages, "working_tree_dirty": bool(
        subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())})
    crawler = MetsSourceCrawler(MetsPublicPilot())
    try:
        if args.pages:
            pages = [int(value) for value in args.pages.split(",")]
        else:
            emit(crawler.prepare_catalogue(filters))
            pages = range(1, filters.max_pages + 1)
        cursor = None
        for page_number in pages:
            if args.pages:
                # Probe a page in isolation; this deliberately proves no preceding coverage.
                crawler = MetsSourceCrawler(MetsPublicPilot())
                crawler._expected_page = page_number
                crawler._prior_digest = "0" * 64
                cursor = f"{page_number}:" + "0" * 64 if page_number != 1 else None
            previous_ids = set(crawler._seen)
            page = crawler._search(filters, cursor)
            metadata = dict(page.metadata)
            metadata.pop("resume_checkpoint", None)
            counts.update(catalogue=metadata["catalogue_lots"], accepted=len(page.items),
                          rejected=metadata["details_rejected"], pages=1,
                          truncated=sum(bool(lot.raw_data.get("detail_html_truncated")) for lot in page.items))
            rejections.update(metadata["rejection_codes"])
            for lot in page.items:
                if lot.external_id in identities:
                    raise ValueError("Duplicate accepted identity")
                identities.add(lot.external_id)
            rejected_ids = sorted(crawler._seen - previous_ids - {lot.external_id for lot in page.items})
            emit({"phase": "page", "page": page_number, "metadata": metadata,
                  "rejected_ids": rejected_ids, "counts": dict(counts)})
            cursor = page.next_cursor
            if not args.pages and cursor is None:
                break
        success = counts["catalogue"] == counts["accepted"] and not counts["rejected"]
        emit({"phase": "complete", "success": success, "terminal_proven": not args.pages and cursor is None,
              "counts": dict(counts), "unique_accepted": len(identities), "rejections": dict(rejections)})
        if not success:
            raise SystemExit(1)
    except Exception as exc:
        emit({"phase": "failed", "error": str(exc), "counts": dict(counts), "rejections": dict(rejections)})
        raise


if __name__ == "__main__":
    main()
