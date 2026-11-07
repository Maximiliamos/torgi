#!/usr/bin/env python3
"""Offline/read-only benchmark and repository audit for the seven-source pipeline."""
from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import tomllib
from typing import Any

SOURCES: tuple[str, ...] = (
    "torgi.gov.ru", "lot-online.ru", "torgi-russia.ru", "bidexpert.ru",
    "m-ets.ru", "cdtrf.ru", "nistp.ru",
)
SOURCE_EVENTS = {"http", "detail_attempt", "cache_hit", "retry", "db_transaction", "queue_wait"}
PIPELINE_SPANS = {"geo_span", "map_build_span", "pipeline_span"}
COUNT_EVENTS = {
    "detail_attempt": "detail_attempts",
    "cache_hit": "cache_hits",
    "retry": "retries",
    "db_transaction": "db_transactions",
}
WORKFLOW_EVENTS = {
    "branch_protection_rule", "check_run", "check_suite", "create", "delete", "deployment",
    "deployment_status", "discussion", "discussion_comment", "fork", "gollum", "issues",
    "issue_comment", "label", "merge_group", "milestone", "page_build", "public",
    "pull_request", "pull_request_review", "pull_request_review_comment", "pull_request_target",
    "push", "registry_package", "release", "repository_dispatch", "schedule", "status", "watch",
    "workflow_call", "workflow_dispatch", "workflow_run",
}


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite non-negative number")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return number


def _count(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def quantile(samples: Sequence[int | float], q: float) -> float | None:
    if not 0 <= q <= 1:
        raise ValueError("q must be between 0 and 1")
    if not samples:
        return None
    values = sorted(_number(value, "sample") for value in samples)
    position = (len(values) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return round(values[lower] + (values[upper] - values[lower]) * (position - lower), 3)


def _timestamp(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _millis(start: object, finish: object) -> float | None:
    begin, end = _timestamp(start), _timestamp(finish)
    if begin is None or end is None:
        return None
    elapsed = (end - begin).total_seconds() * 1000
    if elapsed < 0:
        raise ValueError("finish timestamp precedes start timestamp")
    return round(elapsed, 3)


def _empty_row(source: str) -> dict[str, Any]:
    return {
        "source": source, "status": None, "source_duration_ms": None,
        "pages": None, "seen": None, "inserted": None, "updated": None,
        "changed": None, "unchanged": None, "archived": None,
        "request_latency_p50_ms": None, "request_latency_p95_ms": None,
        "detail_attempts": None, "cache_hits": None, "retries": None,
        "db_transactions": None, "queue_wait_ms": None,
    }


def _snapshot_rows(snapshot: Mapping[str, Any] | None) -> tuple[dict[str, dict[str, Any]], list[str]]:
    rows = {source: _empty_row(source) for source in SOURCES}
    warnings: list[str] = []
    if snapshot is None:
        return rows, warnings
    entries = snapshot.get("sources", [])
    if not isinstance(entries, list):
        raise ValueError("snapshot.sources must be a list")
    observed: set[str] = set()
    for item in entries:
        if not isinstance(item, Mapping):
            raise ValueError("snapshot source entry must be an object")
        source = item.get("source") or item.get("source_system")
        if not isinstance(source, str) or source not in rows:
            raise ValueError(f"unexpected source: {source}")
        if source in observed:
            raise ValueError(f"duplicate source in snapshot: {source}")
        observed.add(source)
        row = rows[source]
        row.update(
            status=item.get("status"),
            pages=item.get("pages_scanned"),
            seen=item.get("items_seen"),
            inserted=item.get("items_inserted"),
            updated=item.get("items_updated"),
            unchanged=item.get("items_unchanged"),
            archived=item.get("items_archived"),
        )
        inserted, updated = row["inserted"], row["updated"]
        row["changed"] = inserted + updated if isinstance(inserted, int) and isinstance(updated, int) else None
        start, finish = item.get("started_at"), item.get("finished_at")
        if bool(start) != bool(finish):
            warnings.append(f"{source}: duration unavailable because only one endpoint timestamp is present")
        row["source_duration_ms"] = _millis(start, finish)
    missing = sorted(set(SOURCES) - observed)
    if missing:
        warnings.append("snapshot missing sources: " + ", ".join(missing))
    return rows, warnings


def _single_span(events: Sequence[Mapping[str, Any]], event_type: str) -> float | None:
    matches = [event for event in events if event.get("type") == event_type]
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError(f"{event_type} requires exactly one end-to-end span, got {len(matches)}")
    return round(_number(matches[0].get("duration_ms"), f"{event_type}.duration_ms"), 3)


def analyze(
    snapshot: Mapping[str, Any] | None = None,
    events: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    rows, warnings = _snapshot_rows(snapshot)
    by_source: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    pipeline_events: list[Mapping[str, Any]] = []
    for index, event in enumerate(events or []):
        if not isinstance(event, Mapping):
            raise ValueError(f"event {index} must be an object")
        event_type = event.get("type")
        if event_type in PIPELINE_SPANS:
            source = event.get("source")
            if source not in (None, "__pipeline__"):
                raise ValueError(f"{event_type} is pipeline-level and must not be attributed to {source}")
            pipeline_events.append(event)
            continue
        if event_type not in SOURCE_EVENTS:
            raise ValueError(f"unknown event type: {event_type}")
        source = event.get("source")
        if not isinstance(source, str) or source not in rows:
            raise ValueError(f"unknown event source: {source}")
        by_source[source].append(event)

    for source, grouped in by_source.items():
        row = rows[source]
        http = [event for event in grouped if event.get("type") == "http"]
        if http:
            latencies = [_number(event.get("duration_ms"), "http.duration_ms") for event in http]
            row["request_latency_p50_ms"] = quantile(latencies, 0.50)
            row["request_latency_p95_ms"] = quantile(latencies, 0.95)
        for event_type, key in COUNT_EVENTS.items():
            matching = [event for event in grouped if event.get("type") == event_type]
            if matching:
                row[key] = sum(_count(event.get("count", 1), f"{event_type}.count") for event in matching)
        waits = [event for event in grouped if event.get("type") == "queue_wait"]
        if waits:
            row["queue_wait_ms"] = round(
                sum(_number(event.get("duration_ms"), "queue_wait.duration_ms") for event in waits), 3
            )

    complete = bool(
        snapshot
        and isinstance(snapshot.get("sources"), list)
        and len(snapshot["sources"]) == len(SOURCES)
        and all(row["status"] == "success" for row in rows.values())
    )
    geo_ms = _single_span(pipeline_events, "geo_span")
    map_ms = _single_span(pipeline_events, "map_build_span")
    raw_critical = _single_span(pipeline_events, "pipeline_span")
    critical_ms = raw_critical if complete else None
    if raw_critical is not None and not complete:
        warnings.append("pipeline_span ignored because the seven-source snapshot is not fully successful")
    return {
        "sources": list(rows.values()),
        "pipeline": {"geo_time_ms": geo_ms, "map_build_time_ms": map_ms, "critical_path_ms": critical_ms},
        "critical_path_ms": critical_ms,
        "complete": complete,
        "warnings": warnings,
    }


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True).stdout


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _requirement_names(path: Path) -> list[str]:
    if not path.is_file():
        return []
    names: set[str] = set()
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "--")):
            continue
        match = re.match(r"([A-Za-z0-9_.-]+)\s*(?:==|~=|>=|<=|!=|>|<|$)", line)
        if match:
            names.add(match.group(1).lower().replace("_", "-"))
    return sorted(names)


def dependency_inventory(repo: Path) -> dict[str, Any]:
    pyproject: dict[str, Any] = {}
    pyproject_path = repo / "pyproject.toml"
    if pyproject_path.is_file():
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8-sig"))
    project = pyproject.get("project", {})
    package: dict[str, Any] = {}
    package_path = repo / "WEB" / "package.json"
    if package_path.is_file():
        package = json.loads(package_path.read_text(encoding="utf-8-sig"))
    runtime_lock = repo / "requirements.lock"
    dev_lock = repo / "requirements-dev.lock"
    web_lock = repo / "WEB" / "package-lock.json"
    return {
        "pyproject_runtime_declared": list(project.get("dependencies", [])),
        "pyproject_dev_declared": list(project.get("optional-dependencies", {}).get("dev", [])),
        "requirements_lock_packages": _requirement_names(runtime_lock),
        "requirements_dev_lock_packages": _requirement_names(dev_lock),
        "web_dependencies": sorted((package.get("dependencies") or {}).keys()),
        "web_dev_dependencies": sorted((package.get("devDependencies") or {}).keys()),
        "lock_sha256": {
            "python_runtime": _sha256(runtime_lock),
            "python_dev": _sha256(dev_lock),
            "web": _sha256(web_lock),
        },
    }


def _module_name(relative: str) -> str:
    return relative.removeprefix("src/").removesuffix(".py").replace("/", ".").removesuffix(".__init__")


def _import_targets(module: str, path: Path, tree: ast.AST) -> set[str]:
    targets: set[str] = set()
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".") if package else []
                drop = node.level - 1
                if drop > len(parts):
                    continue
                prefix = parts[: len(parts) - drop]
                if node.module:
                    prefix.extend(node.module.split("."))
                base = ".".join(prefix)
            else:
                base = node.module or ""
            if base:
                targets.add(base)
            for alias in node.names:
                if alias.name != "*":
                    targets.add(f"{base}.{alias.name}" if base else alias.name)
    return targets


def import_cycles(files: Sequence[Path], repo: Path) -> list[list[str]]:
    modules = {
        _module_name(path.relative_to(repo).as_posix()): path
        for path in files
        if path.suffix == ".py" and path.relative_to(repo).as_posix().startswith("src/bankrotai/")
    }
    graph: dict[str, list[str]] = {}
    for module, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        graph[module] = sorted(
            target for target in _import_targets(module, path, tree) if target in modules and target != module
        )
    state: dict[str, int] = {}
    stack: list[str] = []
    cycles: set[tuple[str, ...]] = set()

    def canonical(nodes: list[str]) -> tuple[str, ...]:
        ring = nodes[:-1] if len(nodes) > 1 and nodes[0] == nodes[-1] else nodes
        rotations = [tuple(ring[index:] + ring[:index]) for index in range(len(ring))]
        return min(rotations) if rotations else tuple()

    def walk(module: str) -> None:
        state[module] = 1
        stack.append(module)
        for dependency in graph[module]:
            if state.get(dependency) == 1:
                cycles.add(canonical(stack[stack.index(dependency):] + [dependency]))
            elif not state.get(dependency):
                walk(dependency)
        stack.pop()
        state[module] = 2

    for module in graph:
        if not state.get(module):
            walk(module)
    return [list(cycle) + [cycle[0]] for cycle in sorted(cycles) if cycle]


def _workflow_triggers(body: str) -> list[str]:
    lines = body.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^on:\s*(.*?)\s*$", line)
        if not match:
            continue
        inline = match.group(1).split("#", 1)[0].strip()
        if inline:
            return sorted(set(re.findall(r"[A-Za-z_]+", inline)) & WORKFLOW_EVENTS)
        events: set[str] = set()
        event_indent: int | None = None
        for nested in lines[index + 1:]:
            if not nested.strip() or nested.lstrip().startswith("#"):
                continue
            indent = len(nested) - len(nested.lstrip(" "))
            if indent == 0:
                break
            key = re.match(r"^\s*([A-Za-z_]+)\s*:", nested)
            if not key:
                continue
            if event_indent is None:
                event_indent = indent
            if indent == event_indent and key.group(1) in WORKFLOW_EVENTS:
                events.add(key.group(1))
        return sorted(events)
    return []


def _concurrency_group(body: str) -> str | None:
    lines = body.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^concurrency:\s*(.*?)\s*$", line)
        if not match:
            continue
        inline = match.group(1).split("#", 1)[0].strip()
        if inline:
            group = re.search(r"group\s*:\s*([^,}]+)", inline)
            return group.group(1).strip() if group else inline
        for nested in lines[index + 1:]:
            if not nested.strip() or nested.lstrip().startswith("#"):
                continue
            if len(nested) == len(nested.lstrip(" ")):
                break
            group = re.match(r"^\s+group:\s*(.*?)\s*$", nested)
            if group:
                return group.group(1).split("#", 1)[0].strip() or None
        return None
    return None


def workflow_inventory(files: Sequence[Path], repo: Path) -> dict[str, Any]:
    workflows: list[dict[str, Any]] = []
    for path in files:
        relative = path.relative_to(repo).as_posix()
        if not relative.startswith(".github/workflows/") or path.suffix not in (".yml", ".yaml"):
            continue
        body = path.read_text(encoding="utf-8-sig")
        runs_on = [
            match.group(1).strip()
            for line in body.splitlines()
            if (match := re.match(r"^\s*runs-on:\s*(.*?)\s*$", line))
        ]
        uses_home_runner = any("self-hosted" in value or "bankrotai-home" in value for value in runs_on)
        workflows.append({
            "file": relative,
            "triggers": _workflow_triggers(body),
            "runs_on": runs_on,
            "home_runner": uses_home_runner,
            "concurrency_group": _concurrency_group(body),
            "cancel_in_progress": bool(re.search(r"(?m)^\s+cancel-in-progress:\s*true\s*(?:#.*)?$", body)),
        })
    home_workflows: list[dict[str, Any]] = [workflow for workflow in workflows if workflow["home_runner"]]
    without_group = [workflow["file"] for workflow in home_workflows if not workflow["concurrency_group"]]
    contention_pairs = 0
    for index, left in enumerate(home_workflows):
        for right in home_workflows[index + 1:]:
            left_group, right_group = left["concurrency_group"], right["concurrency_group"]
            if not left_group or not right_group or left_group != right_group:
                contention_pairs += 1
    return {
        "workflow_count": len(workflows),
        "workflows": workflows,
        "home_runner_workflows": home_workflows,
        "home_runner_without_concurrency_group": without_group,
        "cross_group_contention_candidate_pairs": contention_pairs,
    }


def repository_audit(repo: str | Path) -> dict[str, Any]:
    root = Path(repo).resolve()
    status_before = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    raw = _git(root, "ls-files", "-z").split(b"\0")
    files = [root / item.decode() for item in raw if item and (root / item.decode()).is_file()]
    sizes: list[dict[str, Any]] = [
        {"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size}
        for path in files
    ]
    sizes.sort(key=lambda item: int(item["bytes"]), reverse=True)
    result = {
        "file_count": len(sizes),
        "tracked_bytes": sum(int(item["bytes"]) for item in sizes),
        "largest_files": sizes[:25],
        "internal_import_cycles_possible": import_cycles(files, root),
        "dependencies": dependency_inventory(root),
        **workflow_inventory(files, root),
    }
    status_after = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if status_before != status_after:
        raise RuntimeError("repository audit modified the working tree")
    result["git_status_unchanged"] = True
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--events", type=Path)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if not (args.snapshot or args.events or args.repo):
        parser.error("provide --snapshot, --events and/or --repo")
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8")) if args.snapshot else None
    events = (
        [json.loads(line) for line in args.events.read_text(encoding="utf-8").splitlines() if line.strip()]
        if args.events else None
    )
    report = {
        "benchmark": analyze(snapshot, events) if (snapshot or events) else None,
        "repository_audit": repository_audit(args.repo) if args.repo else None,
    }
    content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.write_text(content, encoding="utf-8")
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
