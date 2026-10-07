from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 1000

# Existing oversized modules are explicitly grandfathered for BAT-308 only.
# The guard prevents new monoliths while incremental extraction reduces this list.
ALLOWLIST = {
    "src/bankrotai/gui.py",
    "src/bankrotai/scrapers.py",
    "src/bankrotai/api.py",
    "src/bankrotai/geo.py",
    "src/bankrotai/services/geo_backfill.py",
    "src/bankrotai/logic.py",
    "src/bankrotai/tasks.py",
    "WEB/src/features/map/MapView.tsx",
}

roots = [ROOT / "src", ROOT / "WEB" / "src"]
extensions = {".py", ".ts", ".tsx", ".js", ".jsx"}
violations: list[tuple[str, int]] = []

for base in roots:
    if not base.exists():
        continue
    for path in base.rglob("*"):
        if not path.is_file() or path.suffix not in extensions:
            continue
        relative = path.relative_to(ROOT).as_posix()
        line_count = sum(1 for _ in path.open("r", encoding="utf-8", errors="replace"))
        if line_count > LIMIT and relative not in ALLOWLIST:
            violations.append((relative, line_count))

if violations:
    rendered = "\n".join(f"- {path}: {lines} lines" for path, lines in sorted(violations))
    raise SystemExit(
        "New >1000-line modules require an ADR or decomposition before merge:\n" + rendered
    )

print(f"module-size-guard: ok; grandfathered={len(ALLOWLIST)}; limit={LIMIT}")
