from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from bankrotai.connectors.etp_sources.common import parse_detail_html, parse_listing_html
from bankrotai.connectors.etp_sources.profiles import PROFILES


def _navigation_url(path: Path) -> str | None:
    navigation = path.with_name(path.stem + "_navigation.json")
    if not navigation.exists():
        return None
    payload = json.loads(navigation.read_text(encoding="utf-8"))
    return payload.get("page_url")


def _lot_summary(lot: Any) -> dict[str, Any]:
    return {
        "external_id": lot.external_id,
        "title": lot.title,
        "start_price": lot.start_price,
        "current_price": lot.current_price,
        "status": lot.auction_status,
        "category": lot.category,
        "address": lot.address,
        "cadastral_number": lot.cadastral_number,
        "area": lot.area,
        "url": lot.lot_url,
    }


def audit(roots: list[Path]) -> dict[str, Any]:
    evidence: dict[str, Path] = {}
    for root in roots:
        for child in root.iterdir():
            if child.is_dir():
                evidence[child.name] = child

    sites: list[dict[str, Any]] = []
    for profile in PROFILES:
        directory = evidence.get(profile.source_id)
        item: dict[str, Any] = {
            "source_id": profile.source_id,
            "profile_status": profile.status,
            "blocked_reason": profile.blocked_reason,
            "evidence": str(directory) if directory else None,
            "pages": [],
            "details": [],
        }
        if directory is None:
            item["audit_status"] = "missing_evidence"
            sites.append(item)
            continue

        first_page_lots: list[Any] = []
        for page_number, filename in (
            (1, "02_catalogue_first.html"),
            (2, "03_catalogue_second_attempt.html"),
        ):
            path = directory / filename
            if not path.exists():
                continue
            try:
                lots = parse_listing_html(profile, path.read_bytes(), page=page_number)
                if page_number == 1:
                    first_page_lots = lots
                item["pages"].append({
                    "page": page_number,
                    "count": len(lots),
                    "ids": [lot.external_id for lot in lots],
                    "sample": [_lot_summary(lot) for lot in lots[:3]],
                })
            except Exception as exc:
                item["pages"].append({"page": page_number, "error": f"{type(exc).__name__}: {exc}"})

        for detail_index, path in enumerate(sorted(directory.glob("lot_*.html"))):
            url = _navigation_url(path)
            expected = first_page_lots[detail_index] if detail_index < len(first_page_lots) else None
            if expected is not None and (not url or profile.lot_url_re.search(url) is None):
                url = expected.lot_url
            if not url:
                item["details"].append({"file": path.name, "error": "missing navigation URL"})
                continue
            try:
                parsed = parse_detail_html(
                    profile, path.read_bytes(), detail_url=url,
                    external_id=expected.external_id if expected is not None else None,
                )
                item["details"].append({"file": path.name, **_lot_summary(parsed)})
            except Exception as exc:
                item["details"].append({
                    "file": path.name,
                    "url": url,
                    "error": f"{type(exc).__name__}: {exc}",
                })

        counts = [page.get("count", 0) for page in item["pages"]]
        distinct = True
        if len(item["pages"]) > 1 and all("ids" in page for page in item["pages"][:2]):
            distinct = item["pages"][0]["ids"] != item["pages"][1]["ids"]
        item["audit_status"] = (
            "blocked" if profile.blocked_reason else
            "parser_error" if any("error" in page for page in item["pages"]) else
            "no_lots" if not counts or max(counts) == 0 else
            "repeated_page" if not distinct else
            "parsed"
        )
        sites.append(item)
    return {"roots": [str(root) for root in roots], "sites": sites}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("roots", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit(args.roots)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
