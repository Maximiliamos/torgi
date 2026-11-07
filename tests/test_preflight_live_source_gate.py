"""Read-only source gate regression tests (zero production dependencies)."""
import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location(
    "preflight_live_source_gate",
    Path(__file__).resolve().parents[1] / "scripts" / "preflight_live_source_gate.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def specs():
    return [
        SimpleNamespace(
            source_id=key,
            filters=type(name, (), {})(),
            reconcile_missing=False,
            require_source_end=True,
            max_batches=500,
        )
        for key, name in (
            (source, module.TYPED.get(source, "OtherFilters"))
            for source in sorted(module.EXPECTED)
        )
    ]


class LiveSourceGateTests(unittest.TestCase):
    def check(self, items=None, paused=None, circuit=None, global_block=False,
              active=None, db_ok=True):
        return module.validate(
            specs() if items is None else items,
            (lambda name: name == paused),
            (lambda name: name == circuit),
            global_block,
            [] if active is None else active,
            db_ok,
        )

    def test_full_coverage(self):
        self.assertTrue(self.check()["pass"])

    def test_missing_duplicate_unexpected(self):
        for mutate in (
            lambda a: a.pop(),
            lambda a: setattr(a[0], "source_id", a[1].source_id),
            lambda a: setattr(a[0], "source_id", "unapproved.example"),
        ):
            with self.subTest(mutate=mutate):
                data = specs()
                mutate(data)
                self.assertFalse(self.check(items=data)["pass"])

    def test_all_fail_closed_gates(self):
        self.assertFalse(self.check(paused="nistp.ru")["pass"])
        self.assertFalse(self.check(circuit="cdtrf.ru")["pass"])
        self.assertFalse(self.check(global_block=True)["pass"])
        self.assertFalse(self.check(active=["lease"])["pass"])
        self.assertFalse(self.check(db_ok=False)["pass"])

    def test_catalogue_contract(self):
        for field, value in (
            ("max_batches", 10),
            ("require_source_end", False),
            ("reconcile_missing", True),
            ("filters", object()),
        ):
            with self.subTest(field=field):
                data = specs()
                target = next(s for s in data if s.source_id == "m-ets.ru")
                setattr(target, field, value)
                self.assertFalse(self.check(items=data)["pass"])


if __name__ == "__main__":
    unittest.main()
