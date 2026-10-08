"""BAT-308 architecture budgets must never grow silently."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_module_sizes.py"


def _read_budgets() -> dict[str, int]:
    module = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    for node in module.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "ALLOWLIST_BUDGETS"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("Explicit allowlist budgets were removed")


def test_grandfathered_modules_have_bounded_caps() -> None:
    caps = _read_budgets()
    assert caps["src/bankrotai/gui.py"] == 7002
    assert caps["src/bankrotai/api.py"] == 2538
    assert caps["src/bankrotai/scrapers.py"] == 3145
    assert caps["src/bankrotai/geo.py"] == 2190
    assert caps["WEB/src/features/map/MapView.tsx"] == 2038
    assert all(cap > 1000 for cap in caps.values())


def test_no_grandfathered_module_exceeds_accepted_baseline() -> None:
    for relpath, cap in _read_budgets().items():
        source = ROOT / relpath
        assert source.exists(), f"Obsolete grandfathering entry: {relpath}"
        lines = len(source.read_text(encoding="utf-8-sig").splitlines())
        assert lines <= cap, f"{relpath}: grew to {lines} lines (cap {cap})"
