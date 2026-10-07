#!/usr/bin/env python3
"""Opt-in encrypted off-host export of an already restore-verified PostgreSQL dump.

No production DB access, no plaintext upload, no implicit destination or key.
Never run as part of the automated Home workflow without owner approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlsplit


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def latest_verified_pair(directory: Path) -> tuple[Path, Path]:
    """Never select a dump on filename/mtime alone; validate metadata and bytes."""
    root = directory.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Backup root must be a directory")
    for metadata in sorted(root.glob("bankrotai-*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True):
        dump = root / (metadata.stem + ".dump")
        try:
            details = json.loads(metadata.read_text(encoding="utf-8-sig"))
            expected_sha = str(details["sha256"]).lower()
            recorded_file = Path(str(details["backup_file"])).resolve(strict=True)
            if not re.fullmatch(r"[a-f0-9]{64}", expected_sha):
                continue
            if details["restore_verification"] != "passed":
                continue
            if not details.get("source_schema_revision") or (
                details.get("restored_schema_revision") != details.get("source_schema_revision")
            ):
                continue
            if not dump.is_file() or dump.resolve(strict=True) != recorded_file:
                continue
            if dump.resolve(strict=True).parent != root or metadata.resolve(strict=True).parent != root:
                continue
            if details.get("size_bytes") != dump.stat().st_size:
                continue
            if sha256_file(dump) != expected_sha:
                continue
        except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError):
            continue
        return dump, metadata
    raise ValueError("No restore-verified backup with matching schema, path, size and SHA-256")


def s3_destination(prefix: str) -> tuple[str, str]:
    parsed = urlsplit(prefix)
    if parsed.scheme != "s3" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("Explicit off-host S3 URI required: s3://BACKUP-BUCKET/prefix")
    if parsed.query or parsed.fragment or not parsed.path.strip("/"):
        raise ValueError("An off-host prefix is required; bucket-root uploads are not allowed")
    if any(segment in {"", ".", ".."} for segment in parsed.path.strip("/").split("/")):
        raise ValueError("Off-host prefix contains an unsafe path component")
    return parsed.netloc, parsed.path.strip("/")


def _run(argv: list[str]) -> str:
    try:
        process = subprocess.run(argv, check=True, capture_output=True, text=True, timeout=3600)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Encrypted export command failed: {argv[0]} {argv[1]}") from exc
    return process.stdout


def export_pair(
    backup_root: Path, *, recipient: str, destination: str, endpoint_url: str | None = None,
) -> list[str]:
    """Encrypt on local disk, upload only ciphertext, verify uploaded byte lengths."""
    if not re.fullmatch(r"age1[0-9a-z]{35,}", recipient):
        raise ValueError("A valid X25519 age1 public recipient is required")
    if endpoint_url is not None and (
        not endpoint_url.startswith("https://") or urlsplit(endpoint_url).username
    ):
        raise ValueError("S3 endpoint must be HTTPS and contain no userinfo")
    bucket, prefix = s3_destination(destination)
    dump, metadata = latest_verified_pair(backup_root)
    if not shutil.which("age") or not shutil.which("aws"):
        raise RuntimeError("age and AWS CLI must be installed before exporting")

    # Collision-resistant upload namespace; never overwrite an older remote backup.
    remote_folder = f"{prefix}/{dump.stem}/{uuid.uuid4().hex}"
    results: list[str] = []
    with tempfile.TemporaryDirectory(prefix="bankrotai-encrypted-", dir=str(backup_root.resolve())) as tmp:
        folder = Path(tmp)
        os.chmod(folder, 0o700)
        for original in (dump, metadata):
            encrypted = folder / (original.name + ".age")
            _run(["age", "-r", recipient, "-o", str(encrypted), str(original)])
            if not encrypted.is_file() or encrypted.stat().st_size <= original.stat().st_size:
                raise RuntimeError("Encryption output is missing or unexpectedly small")
            key = f"{remote_folder}/{encrypted.name}"
            target = f"s3://{bucket}/{key}"
            endpoint_args = ["--endpoint-url", endpoint_url] if endpoint_url else []
            _run(["aws", *endpoint_args, "s3", "cp", str(encrypted), target, "--only-show-errors"])
            head = json.loads(_run(["aws", *endpoint_args, "s3api", "head-object", "--bucket", bucket, "--key", key]))
            if int(head["ContentLength"]) != encrypted.stat().st_size:
                raise RuntimeError("Off-host object size mismatch after ciphertext upload")
            results.append(target)

    # No plaintext ever crosses the upload boundary. Any partial objects are encrypted.
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--s3-prefix", default=os.environ.get("OFFHOST_BACKUP_S3_PREFIX"))
    parser.add_argument("--endpoint-url", default=os.environ.get("OFFHOST_BACKUP_S3_ENDPOINT"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    pair = latest_verified_pair(args.backup_root)
    if args.dry_run:
        print(f"Verified local dump: {pair[0].name}; off-host export not attempted")
        return
    if not args.s3_prefix:
        parser.error("Provide an owner-approved --s3-prefix or OFFHOST_BACKUP_S3_PREFIX")
    recipient = os.environ.get("OFFHOST_AGE_RECIPIENT", "")
    results = export_pair(args.backup_root, recipient=recipient, destination=args.s3_prefix, endpoint_url=args.endpoint_url)
    for uri in results:
        print(f"Encrypted remote object verified: {uri}")


if __name__ == "__main__":
    main()
