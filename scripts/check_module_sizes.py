from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 1000

# Existing oversized modules have an immutable line-budget ratchet.
# Every accepted extraction may lower its cap; expansions require a documented
# code review updating the cap, not silent unlimited grandfathering.
# Snapshot as of accepted main SHA 3bd9e4baa6b2ce339fb51744830211c233628a31.
ALLOWLIST_BUDGETS = {
    "src/bankrotai/gui.py": 7002,
    "src/bankrotai/scrapers.py": 3145,
    "src/bankrotai/api.py": 2538,
    "src/bankrotai/geo.py": 2190,
    "src/bankrotai/services/geo_backfill.py": 1880,
    "src/bankrotai/logic.py": 1257,
    "src/bankrotai/tasks.py": 1296,
    "src/bankrotai/services/ingestion.py": 1185,
    "WEB/src/features/map/MapView.tsx": 2038,
    "WEB/src/lib/api.ts": 1164,
}

roots = [ROOT / "src", ROOT / "WEB" / "src"]
extensions = {".py", ".ts", ".tsx", ".js", ".jsx"}
violations: list[tuple[str, int, int]] = []

for base in roots:
    if not base.exists():
        continue
    for path in base.rglob("*"):
        if not path.is_file() or path.suffix not in extensions:
            continue
        relative = path.relative_to(ROOT).as_posix()
        line_count = sum(1 for _ in path.open("r", encoding="utf-8", errors="replace"))
        allowed_max = ALLOWLIST_BUDGETS.get(relative, LIMIT)
        if line_count > allowed_max:
            violations.append((relative, line_count, allowed_max))

if violations:
    rendered = "\n".join(f"- {path}: {lines} lines > allowed {budget}" for path, lines, budget in sorted(violations))
    raise SystemExit(
        "New >1000-line modules require an ADR or decomposition before merge:\n" + rendered
    )

print(f"module-size-guard: ok; grandfathered={len(ALLOWLIST_BUDGETS)}; limit={LIMIT}")
