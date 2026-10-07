"""Offline acceptance: off-host exporter never sends an unverified/plaintext dump."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import export_verified_backup_offhost as offhost


def verified_pair(root: Path) -> tuple[Path, Path]:
    dump = root / "bankrotai-20261007-000000.dump"
    dump.write_bytes(b"fake pg_dump contents, test only" * 5)
    metadata = root / "bankrotai-20261007-000000.json"
    metadata.write_text(
        json.dumps(
            {
                "backup_file": str(dump),
                "restore_verification": "passed",
                "source_schema_revision": "abc",
                "restored_schema_revision": "abc",
                "sha256": hashlib.sha256(dump.read_bytes()).hexdigest(),
                "size_bytes": dump.stat().st_size,
            }
        ),
        encoding="utf-8",
    )
    return dump, metadata


def test_only_matching_restore_verified_dump_can_be_exported(tmp_path: Path) -> None:
    dump, metadata = verified_pair(tmp_path)
    assert offhost.latest_verified_pair(tmp_path) == (dump, metadata)
    dump.write_bytes(b"tampering detected")
    with pytest.raises(ValueError, match="No restore-verified backup"):
        offhost.latest_verified_pair(tmp_path)


def test_failed_restore_cannot_be_exported(tmp_path: Path) -> None:
    _, meta = verified_pair(tmp_path)
    data = json.loads(meta.read_text())
    data["restore_verification"] = "failed"
    meta.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        offhost.latest_verified_pair(tmp_path)


@pytest.mark.parametrize("uri", [
    "https://example.com/bucket",
    "s3://bucket",
    "s3://bucket/a/../b",
    "s3://bucket/a?token=secret",
])
def test_rejects_missing_or_unsafe_destination(uri: str) -> None:
    with pytest.raises(ValueError):
        offhost.s3_destination(uri)


def test_export_uses_ciphertext_only_and_head_verification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    verified_pair(tmp_path)
    monkeypatch.setattr(offhost.shutil, "which", lambda _: "/usr/bin/fake")
    calls: list[list[str]] = []

    def fake_run(argv: list[str]) -> str:
        calls.append(argv)
        if argv[0] == "age":
            source = Path(argv[-1])
            Path(argv[argv.index("-o") + 1]).write_bytes(source.read_bytes() + b"encrypted-envelope")
        if "head-object" in argv:
            encrypted = [Path(a) for c in calls for a in c if a.endswith(".age") and Path(a).exists()]
            return json.dumps({"ContentLength": encrypted[-1].stat().st_size})
        return ""

    monkeypatch.setattr(offhost, "_run", fake_run)
    remote = offhost.export_pair(
        tmp_path,
        recipient="age1" + "x" * 58,
        destination="s3://separate-dr-bucket/verified/pg",
        endpoint_url="https://s3.regru.cloud",
    )
    assert len(remote) == 2
    assert all(r.endswith(".age") for r in remote)
    uploads = [call for call in calls if "cp" in call]
    assert len(uploads) == 2
    assert all(".age" in call[-3] and call[-2].endswith(".age") for call in uploads)
    assert all(str(tmp_path / "bankrotai-20261007-000000.dump") not in call for call in uploads)
