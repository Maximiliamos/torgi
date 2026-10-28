"""Audit an ETP Smart Capture ZIP locally without exporting PII or network bodies.

Run:
    python scripts/verify_etp_capture.py --input <capture.zip> --output <audit.json>
The script is read-only and never imports the DB or runs Celery.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import ModuleType

_SOURCE = Path(__file__).resolve().parents[1] / "src" / "bankrotai"
for _name, _path in (
    ("bankrotai", _SOURCE),
    ("bankrotai.connectors", _SOURCE / "connectors"),
    ("bankrotai.connectors.registry", _SOURCE / "connectors" / "registry"),
):
    if _name not in sys.modules:
        _pkg = ModuleType(_name)
        _pkg.__path__ = [str(_path)]
        sys.modules[_name] = _pkg

from bankrotai.connectors.registry.etp_capture_replay import inspect_capture


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = inspect_capture(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for entry in report["sources"]:
        print(
            f"{entry['source']:22s} captured={entry['capture_status']:26s} "
            f"candidates={entry['catalogue_candidates']:3d} "
            f"page2={entry['page_2_distinct_in_parser']}"
        )
    print("Read-only report written:", args.output)
    print("No DB, Celery, map publication or archival actions were performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
