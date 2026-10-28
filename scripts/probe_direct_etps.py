"""One joint read-only verification for all 20 direct ETP source profiles.

This command is intended to be run *once* after catalogue adapters have been
implemented, not individually. It never writes to TORGI DB, cache, S3 or Celery.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import requests
import sys
from types import ModuleType

# This probe deliberately avoids loading bankrotai.__init__, the DB layer,
# connector registry and worker runtime. All relevant HTML parsers are pure.
# Import the read-only modules using a package namespace bound to their source
# directory so the isolated CI runner needs only requests + BeautifulSoup.
_ROOT = Path(__file__).resolve().parents[1] / "src" / "bankrotai"
for _name, _directory in (
    ("bankrotai", _ROOT),
    ("bankrotai.connectors", _ROOT / "connectors"),
    ("bankrotai.connectors.registry", _ROOT / "connectors" / "registry"),
):
    if _name not in sys.modules:
        _namespace = ModuleType(_name)
        _namespace.__path__ = [str(_directory)]
        sys.modules[_name] = _namespace

from bankrotai.connectors.registry.direct_etp_catalogue import (
    PROFILES,
    batch_probe,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-candidates-per-site", type=int, default=20)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    parser.add_argument("--confirm-public-network", action="store_true", required=True)
    args = parser.parse_args()
    with requests.Session() as session:
        report = batch_probe(
            session=session, limit=args.max_candidates_per_site,
            delay_seconds=args.delay_seconds,
        )
    report["run_at_utc"] = datetime.now(timezone.utc).isoformat()
    report["all_profile_ids"] = [p.source_id for p in PROFILES]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for source in report["sources"]:
        print(
            f"{source['source']:22s} {source['status']:32s} "
            f"candidates={len(source['candidates'])}"
        )
    print(f"Report: {args.output} | production updates=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
