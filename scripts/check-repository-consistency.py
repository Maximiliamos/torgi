from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    print(f"repository-consistency: {message}", file=sys.stderr)
    raise SystemExit(1)


def normalized_lock(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    pattern = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;]+)")
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            result[match.group(1).lower().replace("_", "-")] = match.group(2)
    return result


with (ROOT / "pyproject.toml").open("rb") as fh:
    version = str(tomllib.load(fh)["project"]["version"])

web_version = str(json.loads((ROOT / "WEB/package.json").read_text(encoding="utf-8"))["version"])
web_lock = json.loads((ROOT / "WEB/package-lock.json").read_text(encoding="utf-8"))
web_lock_version = str(web_lock.get("version"))
web_lock_root_version = str((web_lock.get("packages") or {}).get("", {}).get("version"))
iss = (ROOT / "installer/BankrotAI.iss").read_text(encoding="utf-8")
version_info = (ROOT / "installer/version_info.txt").read_text(encoding="utf-8")
runtime_init = (ROOT / "src/bankrotai/__init__.py").read_text(encoding="utf-8")
if 'version("bankrotai-finder")' not in runtime_init:
    fail("runtime distribution name must match pyproject project name")
if f'__version__ = "{version}"' not in runtime_init:
    fail("frozen/metadata-free runtime version fallback must match pyproject")

checks = {
    "WEB/package.json": web_version,
    "WEB/package-lock.json": web_lock_version,
    "WEB/package-lock.json packages root": web_lock_root_version,
    "installer/BankrotAI.iss": (re.search(r'#define MyAppVersion "([^"]+)"', iss) or [None, "missing"])[1],
    "installer/version_info.txt ProductVersion": (re.search(r"ProductVersion', '([^']+)'", version_info) or [None, "missing"])[1],
    "installer/version_info.txt FileVersion": (re.search(r"FileVersion', '([^']+)'", version_info) or [None, "missing"])[1],
}
for source, candidate in checks.items():
    if candidate != version:
        fail(f"version mismatch: pyproject={version}, {source}={candidate}")

parts = [int(part) for part in version.split(".")]
if len(parts) != 3:
    fail(f"version must be semver MAJOR.MINOR.PATCH, got {version}")
tuple_text = f"({parts[0]}, {parts[1]}, {parts[2]}, 0)"
if version_info.count(tuple_text) < 2:
    fail(f"Windows FixedFileInfo does not match {version}: expected {tuple_text}")

runtime = normalized_lock(ROOT / "requirements.lock")
development = normalized_lock(ROOT / "requirements-dev.lock")
mismatch = {
    name: (runtime[name], development[name])
    for name in sorted(runtime.keys() & development.keys())
    if runtime[name] != development[name]
}
if mismatch:
    fail("runtime/dev lock mismatch: " + json.dumps(mismatch, sort_keys=True))

print(json.dumps({"version": version, "shared_locked_packages": len(runtime.keys() & development.keys())}, sort_keys=True))
