"""Offline regression tests: no DB, network or production mutations."""
import copy
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "preflight_source_recovery",
    Path(__file__).resolve().parents[1] / "scripts" / "preflight_source_recovery.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def snapshot():
    sha = "a" * 40
    items = []
    for name in sorted(mod.SOURCES):
        items.append(dict(source_id=name, paused=False, circuit_open=False,
                          contract_valid=True, status="success", source_end_verified=True,
                          items_failed=0, items_archived=0))
    return dict(expected_sha=sha, checkout_sha=sha, image_sha=sha, parser_sha=sha,
                sources=items, db_read_ok=True, global_network_block=False,
                active_lease_count=0, backup_restore_verified=True,
                backup_sha256_valid=True, disk_free_bytes=10000,
                disk_required_bytes=5000, s3_free_bytes=10000, s3_required_bytes=5000)


class PreflightTests(unittest.TestCase):
    def test_successful_seven_source_coverage(self):
        r = mod.assess(snapshot())
        self.assertTrue(r["allowed"])
        self.assertEqual(7, len(r["sources"]))
        self.assertTrue(all(s["action"] == "reuse_success" for s in r["sources"].values()))

    def test_fail_closed_global_gates(self):
        changes = {"checkout_sha": "b" * 40, "active_lease_count": 1,
                   "db_read_ok": False, "backup_restore_verified": False,
                   "backup_sha256_valid": False, "s3_free_bytes": 1,
                   "disk_free_bytes": 1, "global_network_block": True}
        for key, bad in changes.items():
            with self.subTest(key=key):
                s = snapshot()
                s[key] = bad
                self.assertFalse(mod.assess(s)["allowed"])

    def test_coverage_and_source_blocks(self):
        for change in ("missing", "duplicate", "pause", "circuit", "contract", "archival"):
            with self.subTest(change=change):
                s = snapshot()
                if change == "missing":
                    s["sources"].pop()
                if change == "duplicate":
                    s["sources"][0]["source_id"] = s["sources"][1]["source_id"]
                if change == "pause":
                    s["sources"][0]["paused"] = True
                if change == "circuit":
                    s["sources"][0]["circuit_open"] = True
                if change == "contract":
                    s["sources"][0]["contract_valid"] = False
                if change == "archival":
                    next(x for x in s["sources"] if x["source_id"] == "cdtrf.ru")["items_archived"] = 1
                self.assertFalse(mod.assess(s)["allowed"])

    def test_failed_run_needs_validated_checkpoint_and_no_reconciliation(self):
        s = snapshot()
        row = s["sources"][0]
        row.update(status="failed", reconciliation_enabled=False,
                   checkpoint_durable=True, cursor_validation_passed=True,
                   current_parser_version="v2", current_filter_digest="f" * 64,
                   checkpoint=dict(source_id=row["source_id"], cursor="page:17",
                                   run_sha=s["expected_sha"], parser_version="v2",
                                   filter_digest="f" * 64))
        self.assertEqual("candidate_only_no_writes",
                         mod.assess(s)["sources"][row["source_id"]]["action"])
        for key, value in [("parser_version", "v1"), ("filter_digest", "0" * 64),
                           ("cursor", ""), ("run_sha", "b" * 40)]:
            with self.subTest(key=key):
                bad = copy.deepcopy(s)
                bad["sources"][0]["checkpoint"][key] = value
                self.assertFalse(mod.assess(bad)["allowed"])
        bad = copy.deepcopy(s)
        bad["sources"][0]["reconciliation_enabled"] = True
        self.assertFalse(mod.assess(bad)["allowed"])


if __name__ == "__main__":
    unittest.main()
