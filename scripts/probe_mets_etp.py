"""Read-only official МЭТС sale-card pilot. No TORGI database writes.

Does one public apartment catalogue GET and up to --max-lots detail GETs.
The catalogue is not complete; no archival or Celery tasks are triggered.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from bankrotai.connectors.registry.mets_public import MetsPublicPilot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-lots", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confirm-network-access", action="store_true", required=True)
    args = parser.parse_args()
    report = MetsPublicPilot().preview(max_lots=args.max_lots)
    report["created_at"] = datetime.now(timezone.utc).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"МЭТС: найдено={report['discovered']}, проверено={report['parsed']}, "
        f"ошибок={report['failed']}; сохранено в {args.output}"
    )
    return 0 if report["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
