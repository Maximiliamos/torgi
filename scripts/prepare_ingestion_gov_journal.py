"""Re-normalize preserved primary ingestion snapshots, in bounded groups."""
import argparse
import base64
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys

import prepare_verified_gov_repair


def prepare(batch, root, output):
    for receipt in batch:
        body = base64.b64decode(receipt["snapshot_base64"], validate=True)
        if hashlib.sha256(body).hexdigest() != receipt["sha256"]:
            raise ValueError("Preserved primary content changed")
        path = root / "gov-primary-json" / (receipt["external_id"].split(":", 1)[1] + "-" + receipt["sha256"] + ".json")
        path.write_bytes(body)
    original_input = sys.stdin
    captured = io.StringIO()
    try:
        sys.stdin = io.StringIO(json.dumps(batch))
        with redirect_stdout(captured):
            prepare_verified_gov_repair.main()
    finally:
        sys.stdin = original_input
    items = json.loads(captured.getvalue())["items"]
    for receipt, item in zip(batch, items, strict=True):
        output.write(json.dumps({**item, "identity_verified": True,
                                "external_id": receipt["external_id"],
                                "checked_at_utc": receipt["checked_at_utc"],
                                "sha256": receipt["sha256"],
                                "ingestion_run_id": receipt["ingestion_run_id"],
                                "scope": "official_ingestion_api_fields", "database_writes": 0},
                               ensure_ascii=True, default=str) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(args.output).parent
    (root / "gov-primary-json").mkdir(exist_ok=True)
    batch, count, expected = [], 0, None
    with Path(args.input).open(encoding="utf-8-sig") as source, Path(args.output).open("w", encoding="utf8") as output:
        for line in source:
            receipt = json.loads(line)
            if receipt.get("event"):
                expected = receipt["selected"]
                continue
            batch.append(receipt)
            if len(batch) == 100:
                prepare(batch, root, output)
                count += len(batch)
                batch = []
                if count % 1000 == 0:
                    print(json.dumps({"prepared": count, "database_writes": 0}), flush=True)
        if batch:
            prepare(batch, root, output)
            count += len(batch)
    if count != expected:
        raise ValueError("Primary ingestion export was incomplete")
    print(json.dumps({"prepared": count, "database_writes": 0}))


if __name__ == "__main__":
    main()
