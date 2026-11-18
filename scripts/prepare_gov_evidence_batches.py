"""Stream primary journals into bounded exact-ID evidence files; no DB access."""
import argparse
import base64
from contextlib import redirect_stdout
from datetime import datetime
import io
import json
from pathlib import Path
import sys

import prepare_verified_gov_repair


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".artifacts/finish")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root, out = Path(args.root), Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    latest = {}
    journals = [root / name for name in ("torgi-gov-primary-verification.jsonl",
                 "gov-export-verification.jsonl", "gov-remaining-direct-verification.jsonl")]
    for path in journals:
        with path.open("rb") as handle:
            while True:
                offset = handle.tell()
                line = handle.readline()
                if not line:
                    break
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if record.get("identity_verified"):
                    key = record["external_id"]
                    stamp = datetime.fromisoformat(record["checked_at_utc"])
                    if key not in latest or stamp > latest[key][0]:
                        latest[key] = (stamp, path, offset)
    indexed = sorted(latest.items())
    for start in range(0, len(indexed), 1000):
        evidence = []
        direct = []
        for identity, (_stamp, path, offset) in indexed[start:start + 1000]:
            with path.open("rb") as handle:
                handle.seek(offset)
                receipt = json.loads(handle.readline())
            if receipt.get("scope") == "official_export_fields":
                evidence.append({key: receipt[key] for key in ("lot_id", "normalized", "verification")})
            else:
                capture = root / "gov-primary-json" / (identity.split(":", 1)[1] + "-" + receipt["sha256"] + ".json")
                direct.append({**receipt, "snapshot_base64": base64.b64encode(capture.read_bytes()).decode()})
        if direct:
            output = io.StringIO()
            original_input = sys.stdin
            try:
                sys.stdin = io.StringIO(json.dumps(direct))
                # The parser's individual bound remains 100 records.
                for offset in range(0, len(direct), 100):
                    sys.stdin = io.StringIO(json.dumps(direct[offset:offset + 100]))
                    output = io.StringIO()
                    with redirect_stdout(output):
                        prepare_verified_gov_repair.main()
                    evidence.extend(json.loads(output.getvalue())["items"])
            finally:
                sys.stdin = original_input
        evidence.sort(key=lambda row: row["lot_id"])
        folder = out / f"group-{start // 1000 + 1:03d}"
        folder.mkdir(exist_ok=True)
        for offset in range(0, len(evidence), 100):
            path = folder / f"input-{offset // 100 + 1:02d}-evidence.json"
            path.write_text(json.dumps({"items": evidence[offset:offset + 100], "database_writes": 0},
                                       ensure_ascii=True, default=str), encoding="utf8")
        print(json.dumps({"group": folder.name, "prepared": len(evidence), "total": len(indexed),
                          "database_writes": 0}), flush=True)
    (out / "manifest.json").write_text(json.dumps({"selected": len(indexed), "groups": (len(indexed) + 999) // 1000,
                                                       "database_writes": 0}), encoding="utf8")


if __name__ == "__main__":
    main()
