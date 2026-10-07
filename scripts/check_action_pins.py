from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
PIN = re.compile(r"^[0-9a-f]{40}$")
violations: list[str] = []

for path in sorted(WORKFLOWS.glob("*.y*ml")):
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        match = re.search(r"\buses:\s*([^\s#]+)", line)
        if not match:
            continue
        target = match.group(1)
        if target.startswith("./") or target.startswith("docker://"):
            continue
        if "@" not in target:
            violations.append(f"{path.relative_to(ROOT)}:{number}: missing @ ref: {target}")
            continue
        action, ref = target.rsplit("@", 1)
        if not PIN.fullmatch(ref):
            violations.append(
                f"{path.relative_to(ROOT)}:{number}: mutable action ref {action}@{ref}"
            )

if violations:
    raise SystemExit(
        "GitHub Actions must be pinned to immutable 40-char commit SHAs:\n"
        + "\n".join(f"- {item}" for item in violations)
    )

print("action-pin-guard: ok")
