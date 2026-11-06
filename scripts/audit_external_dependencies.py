"""Read-only inventory of literal external domains in production-related files.

Static source inspection, not DNS tracing or proof of the provider's location.
Never performs network requests.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOREIGN_PROVIDERS = {
    "anthropic.com": "Anthropic API",
    "cloudflare.com": "Cloudflare",
    "cloudflarestorage.com": "Cloudflare R2",
    "github.com": "GitHub",
    "githubusercontent.com": "GitHub",
    "google.com": "Google",
    "googleapis.com": "Google APIs",
    "gstatic.com": "Google assets",
    "jsdelivr.net": "jsDelivr CDN",
    "npmjs.org": "npm registry",
    "openai.com": "OpenAI API",
    "pages.dev": "Cloudflare Pages",
    "pypi.org": "PyPI",
    "unpkg.com": "unpkg CDN",
    "workers.dev": "Cloudflare Workers",
}
RUSSIAN_PROVIDERS = {
    "reg.ru": "REG.RU",
    "gitflic.ru": "GitFlic",
    "yandex.ru": "Yandex",
    "yandex.net": "Yandex",
    "yandex.com": "Yandex",
}
# Keep scope narrow: exclude vendored JS, lockfiles and prose documentation.
SCAN_ROOTS = (
    "src/bankrotai", "WEB/src", "WEB/functions", "WEB/api-proxy", "WEB/edge-proxy",
    ".github/workflows",
)
SCAN_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".jsx", ".yml", ".yaml", ".json", ".jsonc", ".css"}
EXACT_FILES = (
    ".env.example", "pyproject.toml", "docker-compose.yml", "docker-compose.test.yml",
    "Dockerfile", "WEB/docker-compose.yml", "WEB/package.json", "WEB/.env.example",
    "WEB/Dockerfile", "WEB/Dockerfile.api",
)
URL_HOST = re.compile(r"\b(?:https?|wss?)://(?P<host>[a-z0-9][a-z0-9.-]*)(?::\d+)?", re.IGNORECASE)
LITERAL_DOMAIN = re.compile(
    r"(?<![a-z0-9_.-])(?:[a-z0-9-]+\.)+(?:ru|com|net|org|io|dev|app|cloud|online|рф)(?![a-z0-9_.-])",
    re.IGNORECASE,
)


def category(host: str) -> tuple[str, str]:
    host = host.lower().rstrip(".")
    if host in {"localhost", "host.docker.internal"} or host.endswith(".local"):
        return "local", "Local host"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        private_ranges = tuple(ipaddress.ip_network(network) for network in (
            "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7",
        ))
        if address.is_loopback or address.is_link_local or any(address in network for network in private_ranges):
            return "local", "Local IP"
        return "review", "Public/special-purpose IP: location not verified"
    for suffix, provider in FOREIGN_PROVIDERS.items():
        if host == suffix or host.endswith("." + suffix):
            return "foreign", provider
    for suffix, provider in RUSSIAN_PROVIDERS.items():
        if host == suffix or host.endswith("." + suffix):
            return "russian", provider
    if host.endswith(".ru") or host.endswith(".рф"):
        return "review", "RU domain, provider/location not verified"
    return "review", "Provider/location not verified"


def candidate_files(root: Path) -> list[Path]:
    paths = {root / path for path in EXACT_FILES}
    for folder in SCAN_ROOTS:
        base = root / folder
        if base.is_dir():
            paths.update(p for p in base.rglob("*") if p.is_file() and p.suffix in SCAN_SUFFIXES)
    return sorted(
        p for p in paths
        if p.is_file()
        and p.stat().st_size < 1_000_000
        and not any(part in {"__pycache__", "node_modules", "assets", "dist", "build"} for part in p.parts)
        and not p.name.endswith((".lock", ".min.js"))
    )


def inventory(root: Path) -> dict:
    findings: dict[tuple[str, str], dict] = {}
    for path in candidate_files(root):
        relative = path.relative_to(root).as_posix()
        for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            hosts = {m.group("host").lower() for m in URL_HOST.finditer(line)}
            hosts.update(m.group().lower() for m in LITERAL_DOMAIN.finditer(line))
            for host in hosts:
                group, provider = category(host)
                item = findings.setdefault((host, relative), {
                    "host": host, "category": group, "provider": provider,
                    "path": relative, "lines": [],
                })
                if len(item["lines"]) < 10:
                    item["lines"].append(line_no)
    rows = sorted(findings.values(), key=lambda x: (x["category"], x["host"], x["path"]))
    summary = dict(sorted({
        group: sum(row["category"] == group for row in rows)
        for group in ("foreign", "russian", "review", "local")
    }.items()))
    return {
        "scope": "Literal domain strings in selected code/config paths; not runtime requests or hosting location",
        "network_requests_performed": 0,
        "summary": summary,
        "findings": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, help="Save JSON report (defaults to stdout)")
    parser.add_argument("--fail-on-foreign", action="store_true", help="Opt-in policy gate; not enabled by default")
    args = parser.parse_args()
    report = inventory(args.root.resolve())
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 2 if args.fail_on_foreign and report["summary"]["foreign"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
