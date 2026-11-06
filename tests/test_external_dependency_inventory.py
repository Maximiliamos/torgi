"""Tests for the offline external-domain inventory."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_external_dependencies.py"
SPEC = importlib.util.spec_from_file_location("external_audit", SCRIPT)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def test_provider_categories_use_known_suffixes_not_tld_guesswork() -> None:
    assert AUDIT.category("api.openai.com") == ("foreign", "OpenAI API")
    assert AUDIT.category("my-site.pages.dev") == ("foreign", "Cloudflare Pages")
    assert AUDIT.category("storage.reg.ru") == ("russian", "REG.RU")
    assert AUDIT.category("api.example.ru")[0] == "review"
    assert AUDIT.category("198.51.100.23")[0] == "review"
    assert AUDIT.category("localhost")[0] == "local"


def test_inventory_is_offline_excludes_vendored_assets_and_lockfiles(tmp_path: Path) -> None:
    runtime = tmp_path / "src/bankrotai/service.py"
    runtime.parent.mkdir(parents=True)
    runtime.write_text('url = "https://api.openai.com/v1"\ncdn = "https://api.reg.ru/v1"\n', encoding="utf-8")
    assets = tmp_path / "src/bankrotai/assets/vendor.js"
    assets.parent.mkdir()
    assets.write_text('"https://cdn.cloudflare.com"', encoding="utf-8")
    lock = tmp_path / "WEB/package-lock.json"
    lock.parent.mkdir()
    lock.write_text('"https://registry.npmjs.org"', encoding="utf-8")
    result = AUDIT.inventory(tmp_path)
    hosts = {f["host"] for f in result["findings"]}
    assert hosts == {"api.openai.com", "api.reg.ru"}
    assert result["summary"]["foreign"] == 1
    assert result["network_requests_performed"] == 0


def test_strict_policy_is_opt_in_and_report_is_json(tmp_path: Path) -> None:
    target = tmp_path / "WEB/src/runtime.ts"
    target.parent.mkdir(parents=True)
    target.write_text('fetch("https://api.anthropic.com")\n', encoding="utf-8")
    common = [sys.executable, str(SCRIPT), "--root", str(tmp_path)]
    default = subprocess.run(common, capture_output=True, text=True, check=False)
    assert default.returncode == 0
    assert json.loads(default.stdout)["summary"]["foreign"] == 1
    strict = subprocess.run(common + ["--fail-on-foreign"], capture_output=True, text=True, check=False)
    assert strict.returncode == 2
