"""Fail-closed local backup/disk/S3 capacity evidence gate.

Read-only: verifies existing dump and signed external quota attestation.
A provider API adapter is required to *produce* S3 evidence; signing alone
does not prove that numbers originated from the provider.
"""
import argparse
import hashlib
import hmac
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

MIN_DISK_BYTES = 10 * 1024 ** 3
MIN_S3_BYTES = 512 * 1024 ** 2


def utc_time(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp is not timezone-aware")
    return parsed.astimezone(timezone.utc)


def canonical_attestation(data):
    return json.dumps(
        {key: data[key] for key in (
            "bucket", "checked_at", "free_bytes", "required_bytes", "source"
        )},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def assess(backup_dir, evidence_path, hmac_key, *,
           now=None, free_disk_bytes=None, min_disk_bytes=MIN_DISK_BYTES,
           min_s3_bytes=MIN_S3_BYTES, expected_bucket=None):
    errors = []
    now = now or datetime.now(timezone.utc)
    backup_dir = Path(backup_dir)
    evidence_path = Path(evidence_path)
    if not backup_dir.is_dir():
        errors.append("backup directory not found")
    backups = sorted(backup_dir.glob("bankrotai-*.json"), reverse=True) if backup_dir.is_dir() else []
    if not backups:
        errors.append("no backup metadata")
    else:
        try:
            metadata_path = backups[0]
            metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
            dump_path = metadata_path.with_suffix(".dump")
            if not dump_path.is_file() or dump_path.resolve() != Path(metadata["backup_file"]).resolve():
                raise ValueError("backup dump path mismatch or missing")
            if metadata.get("restore_verification") != "passed":
                raise ValueError("isolated restore not verified")
            if metadata.get("map_storage_anomaly") is True:
                raise ValueError("map storage anomaly")
            if not metadata.get("source_schema_revision"):
                raise ValueError("missing backup schema revision")
            if not isinstance(metadata.get("sha256"), str) or len(metadata["sha256"]) != 64:
                raise ValueError("invalid backup checksum")
            created = utc_time(metadata["created_at"])
            age = (now - created).total_seconds()
            if not 0 <= age <= 86400:
                raise ValueError("backup proof stale or from the future")
            hasher = hashlib.sha256()
            with dump_path.open("rb") as reader:
                for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                    hasher.update(chunk)
            if not hmac.compare_digest(hasher.hexdigest(), metadata["sha256"].lower()):
                raise ValueError("backup SHA256 mismatch")
            if dump_path.stat().st_size != metadata.get("size_bytes"):
                raise ValueError("backup size mismatch")
            minimum_disk = max(min_disk_bytes, dump_path.stat().st_size * 2)
            if free_disk_bytes is None:
                free_disk_bytes = shutil.disk_usage(backup_dir).free
            if type(free_disk_bytes) is not int or free_disk_bytes < minimum_disk:
                raise ValueError("insufficient disk bytes for next backup and restore")
        except (OSError, KeyError, ValueError, TypeError) as exc:
            errors.append("backup/disk: " + str(exc))

    try:
        if not hmac_key or len(hmac_key) < 32:
            raise ValueError("S3 evidence verification key absent or too short")
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        signature = evidence.get("hmac_sha256", "")
        if not isinstance(signature, str) or len(signature) != 64:
            raise ValueError("missing S3 evidence HMAC")
        expected = hmac.new(hmac_key.encode("utf-8"),
                            canonical_attestation(evidence), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature.lower(), expected):
            raise ValueError("S3 evidence signature mismatch")
        if evidence["source"] != "provider_quota_api":
            raise ValueError("S3 evidence is not provider quota evidence")
        if not isinstance(expected_bucket, str) or not expected_bucket or evidence["bucket"] != expected_bucket:
            raise ValueError("S3 evidence bucket differs from configured map bucket")
        age = (now - utc_time(evidence["checked_at"])).total_seconds()
        if not 0 <= age <= 3600:
            raise ValueError("S3 evidence stale or from the future")
        free = evidence["free_bytes"]
        required = evidence["required_bytes"]
        if type(free) is not int or type(required) is not int or required < min_s3_bytes or free < required:
            raise ValueError("S3 free/required capacity unproven")
    except (OSError, KeyError, ValueError, TypeError) as exc:
        errors.append("S3 capacity: " + str(exc))
    return {"allowed": not errors, "dry_run": True, "errors": errors}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--s3-evidence", type=Path, required=True)
    parser.add_argument("--expected-bucket", required=True)
    args = parser.parse_args()
    import os
    result = assess(args.backup_dir, args.s3_evidence,
                    os.environ.get("TORGI_CAPACITY_EVIDENCE_HMAC_KEY", ""),
                    expected_bucket=args.expected_bucket)
    print("CAPACITY_PREFLIGHT=" + json.dumps(result, sort_keys=True))
    return 0 if result["allowed"] else 2


if __name__ == "__main__":
    sys.exit(main())
