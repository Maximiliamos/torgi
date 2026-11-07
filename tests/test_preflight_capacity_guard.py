"""Synthetic backup/quota evidence tests; never contacts S3."""
import hashlib
import hmac
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "preflight_capacity_guard",
    Path(__file__).resolve().parents[1] / "scripts" / "preflight_capacity_guard.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.evidence_file = self.root / "s3.json"
        self.key = "this-is-a-synthetic-test-key-of-adequate-length"
        self.now = datetime(2026, 11, 8, tzinfo=timezone.utc)
        self.dump = self.backups / "bankrotai-20261108-000000.dump"
        self.dump.write_bytes(b"synthetic pg_dump fixture")
        self.meta_path = self.dump.with_suffix(".json")
        self.meta = dict(backup_file=str(self.dump), restore_verification="passed",
                         created_at=self.now.isoformat(), source_schema_revision="head",
                         size_bytes=self.dump.stat().st_size,
                         sha256=hashlib.sha256(self.dump.read_bytes()).hexdigest(),
                         map_storage_anomaly=False)
        self.save_meta()
        self.evidence = dict(source="provider_quota_api", bucket="test-bucket",
                             checked_at=self.now.isoformat(),
                             free_bytes=10000, required_bytes=1000)
        self.save_evidence()

    def save_meta(self):
        self.meta_path.write_text(json.dumps(self.meta))

    def save_evidence(self):
        self.evidence["hmac_sha256"] = hmac.new(
            self.key.encode(), module.canonical_attestation(self.evidence),
            hashlib.sha256,
        ).hexdigest()
        self.evidence_file.write_text(json.dumps(self.evidence))

    def assess(self, key=None):
        return module.assess(
            self.backups, self.evidence_file, self.key if key is None else key,
            now=self.now, free_disk_bytes=10000, min_disk_bytes=1000,
            min_s3_bytes=1000,
        )

    def test_complete_signed_evidence(self):
        self.assertTrue(self.assess()["allowed"])

    def test_tampered_dump_blocks(self):
        self.dump.write_bytes(b"tampered")
        self.assertFalse(self.assess()["allowed"])

    def test_restore_missing_and_stale_proof(self):
        self.meta["restore_verification"] = "not-requested"
        self.save_meta()
        self.assertFalse(self.assess()["allowed"])
        self.meta["restore_verification"] = "passed"
        self.meta["created_at"] = "2026-11-06T00:00:00+00:00"
        self.save_meta()
        self.assertFalse(self.assess()["allowed"])

    def test_missing_or_stale_s3_proof(self):
        self.evidence_file.unlink()
        self.assertFalse(self.assess()["allowed"])
        self.evidence["checked_at"] = "2026-11-07T00:00:00+00:00"
        self.save_evidence()
        self.assertFalse(self.assess()["allowed"])

    def test_s3_quota_and_signature(self):
        self.evidence["free_bytes"] = 1
        self.save_evidence()
        self.assertFalse(self.assess()["allowed"])
        self.evidence["free_bytes"] = 10000
        self.save_evidence()
        self.assertFalse(self.assess(key="wrong-key-that-is-also-long-enough")["allowed"])
        self.evidence["source"] = "manual_guess"
        self.save_evidence()
        self.assertFalse(self.assess()["allowed"])


if __name__ == "__main__":
    unittest.main()
