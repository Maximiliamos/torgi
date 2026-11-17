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


class AcceptanceCrawler(MetsSourceCrawler):
    def __init__(self, pilot):
        super().__init__(pilot)
        self.observed_pages = {}

    def _parsed_page(self, number):
        page = super()._parsed_page(number)
        self.observed_pages[number] = page
        return page


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", help="Comma-separated canary pages; omit for full walk")
    parser.add_argument("--output", required=True)
    parser.add_argument("--resume", action="store_true", help="Resume a same-SHA saved checkpoint")
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    filters = MetsSyncFilters()
    counts = Counter()
    rejections = Counter()
    identities = set()
    checkpoint_path = output.with_suffix(output.suffix + ".checkpoint.json")

    def emit(value):
        value.update(at_utc=datetime.now(timezone.utc).isoformat(), head=sha,
                     database_writes=0, reconcile_missing=False, safe_for_archival=False)
        line = json.dumps(value, ensure_ascii=True, default=str)
        with output.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        print(line, flush=True)

    emit({"phase": "start", "pages": args.pages, "working_tree_dirty": bool(
        subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())})
    crawler = AcceptanceCrawler(MetsPublicPilot())
    try:
        cursor = None
        if args.resume:
            if args.pages:
                raise ValueError("Canary pages cannot claim resumed full coverage")
            saved = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            if saved["head"] != sha:
                raise ValueError("Checkpoint SHA differs; a new code revision needs a fresh pass")
            crawler._prepared_pages = {int(key): value for key, value in saved["prepared_pages"].items()}
            cursor = crawler.restore_checkpoint(saved["crawler"])
            if cursor is None:
                raise ValueError("Checkpoint does not contain a valid continuation")
            counts.update(saved["counts"])
            rejections.update(saved["rejections"])
            identities.update(saved["accepted_ids"])
            pages = range(crawler._expected_page, filters.max_pages + 1)
            emit({"phase": "resume", "next_page": crawler._expected_page, "counts": dict(counts)})
        elif args.pages:
            pages = [int(value) for value in args.pages.split(",")]
        else:
            emit(crawler.prepare_catalogue(filters))
            pages = range(1, filters.max_pages + 1)
        for page_number in pages:
            if args.pages:
                # Probe a page in isolation; this deliberately proves no preceding coverage.
                crawler = AcceptanceCrawler(MetsPublicPilot())
                crawler._expected_page = page_number
                crawler._prior_digest = "0" * 64
                cursor = f"{page_number}:" + "0" * 64 if page_number != 1 else None
            previous_ids = set(crawler._seen)
            page = crawler._search(filters, cursor)
            metadata = dict(page.metadata)
            checkpoint = metadata.pop("resume_checkpoint", None)
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
            if not args.pages and checkpoint is not None:
                saved = {"head": sha, "crawler": checkpoint, "counts": dict(counts),
                         "rejections": dict(rejections), "accepted_ids": sorted(identities),
                         "prepared_pages": {**crawler._prepared_pages, **crawler.observed_pages}}
                temporary = checkpoint_path.with_suffix(".tmp")
                temporary.write_text(json.dumps(saved, ensure_ascii=True), encoding="utf-8")
                temporary.replace(checkpoint_path)
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
