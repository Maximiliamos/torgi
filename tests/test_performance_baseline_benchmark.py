from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.read_only_pipeline import SOURCES, analyze, quantile, repository_audit  # noqa: E402

BASELINE = ROOT / "benchmarks" / "fixtures" / "run-42393273402.json"


def successful_snapshot() -> dict[str, object]:
    return {"sources": [{"source": source, "status": "success"} for source in SOURCES]}


def test_percentiles_and_missing_metrics() -> None:
    assert quantile([10, 20, 30, 40, 50], 0.95) == 48
    assert quantile([], 0.95) is None
    with pytest.raises(ValueError, match="q must"):
        quantile([1], 1.1)
    report = analyze(successful_snapshot(), [{"type": "http", "source": SOURCES[0], "duration_ms": 12}])
    row = report["sources"][0]
    assert row["request_latency_p50_ms"] == 12
    assert row["cache_hits"] is None
    assert row["retries"] is None
    assert row["db_transactions"] is None
    assert row["queue_wait_ms"] is None


def test_confirmed_baseline_is_partial_and_stable() -> None:
    snapshot = json.loads(BASELINE.read_text(encoding="utf-8"))
    report = analyze(snapshot)
    assert report["complete"] is False
    assert sum(row["pages"] for row in report["sources"]) == 4346
    assert sum(row["seen"] for row in report["sources"]) == 37597
    assert sum(row["changed"] for row in report["sources"]) == 5689
    assert next(row for row in report["sources"] if row["source"] == "cdtrf.ru")["changed"] == 1585
    assert report["pipeline"]["critical_path_ms"] is None


def test_source_and_pipeline_telemetry_are_separate() -> None:
    events = [
        {"type": "http", "source": SOURCES[0], "duration_ms": 10},
        {"type": "http", "source": SOURCES[0], "duration_ms": 30},
        {"type": "detail_attempt", "source": SOURCES[0], "count": 2},
        {"type": "cache_hit", "source": SOURCES[0]},
        {"type": "retry", "source": SOURCES[0]},
        {"type": "db_transaction", "source": SOURCES[0], "count": 3},
        {"type": "queue_wait", "source": SOURCES[0], "duration_ms": 7},
        {"type": "geo_span", "duration_ms": 100},
        {"type": "map_build_span", "source": "__pipeline__", "duration_ms": 50},
        {"type": "pipeline_span", "duration_ms": 500},
    ]
    report = analyze(successful_snapshot(), events)
    row = report["sources"][0]
    assert row["request_latency_p50_ms"] == 20
    assert row["request_latency_p95_ms"] == 29
    assert row["detail_attempts"] == 2
    assert row["cache_hits"] == 1
    assert row["retries"] == 1
    assert row["db_transactions"] == 3
    assert row["queue_wait_ms"] == 7
    assert report["pipeline"] == {"geo_time_ms": 100, "map_build_time_ms": 50, "critical_path_ms": 500}


def test_failed_snapshot_cannot_publish_critical_path() -> None:
    snapshot = successful_snapshot()
    sources = snapshot["sources"]
    assert isinstance(sources, list)
    sources[0]["status"] = "failed"
    report = analyze(snapshot, [{"type": "pipeline_span", "duration_ms": 123}])
    assert report["critical_path_ms"] is None
    assert any("ignored" in warning for warning in report["warnings"])


def test_invalid_telemetry_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown event source"):
        analyze(events=[{"type": "http", "source": "other", "duration_ms": 1}])
    with pytest.raises(ValueError, match="non-negative integer"):
        analyze(events=[{"type": "retry", "source": SOURCES[0], "count": -1}])
    with pytest.raises(ValueError, match="pipeline-level"):
        analyze(events=[{"type": "geo_span", "source": SOURCES[0], "duration_ms": 1}])
    with pytest.raises(ValueError, match="exactly one"):
        analyze(successful_snapshot(), [
            {"type": "pipeline_span", "duration_ms": 1},
            {"type": "pipeline_span", "duration_ms": 2},
        ])
    with pytest.raises(ValueError, match="precedes"):
        analyze({"sources": [{
            "source": SOURCES[0], "status": "success",
            "started_at": "2026-11-08T01:00:00Z", "finished_at": "2026-11-08T00:00:00Z",
        }]})


def test_duplicate_snapshot_source_is_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate source"):
        analyze({"sources": [{"source": SOURCES[0]}, {"source": SOURCES[0]}]})


def test_repository_audit_detects_cycles_triggers_dependencies_and_is_read_only(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "src/bankrotai").mkdir(parents=True)
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / "WEB").mkdir()
    (tmp_path / "src/bankrotai/__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src/bankrotai/a.py").write_text("from . import b\n", encoding="utf-8")
    (tmp_path / "src/bankrotai/b.py").write_text("from . import a\n", encoding="utf-8")
    (tmp_path / ".github/workflows/home.yml").write_text(
        "name: home\non: [push, workflow_dispatch]\nconcurrency:\n  group: home-test\n"
        "jobs:\n  run:\n    runs-on: [self-hosted, Windows, bankrotai-home]\n    steps: []\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="fixture"\nversion="0"\ndependencies=["httpx>=0.27"]\n'
        '[project.optional-dependencies]\ndev=["pytest>=9"]\n',
        encoding="utf-8",
    )
    (tmp_path / "requirements.lock").write_text("httpx==0.28.1\n", encoding="utf-8")
    (tmp_path / "requirements-dev.lock").write_text("pytest==9.0.3\n", encoding="utf-8")
    (tmp_path / "WEB/package.json").write_text(
        json.dumps({"dependencies": {"react": "1"}, "devDependencies": {"vite": "1"}}), encoding="utf-8"
    )
    (tmp_path / "WEB/package-lock.json").write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    before = subprocess.run(
        ["git", "-C", str(tmp_path), "status", "--porcelain=v1", "--untracked-files=all"],
        check=True, capture_output=True,
    ).stdout
    audit = repository_audit(tmp_path)
    after = subprocess.run(
        ["git", "-C", str(tmp_path), "status", "--porcelain=v1", "--untracked-files=all"],
        check=True, capture_output=True,
    ).stdout
    assert before == after
    assert audit["git_status_unchanged"] is True
    assert audit["workflow_count"] == 1
    workflow = audit["home_runner_workflows"][0]
    assert workflow["triggers"] == ["push", "workflow_dispatch"]
    assert workflow["concurrency_group"] == "home-test"
    assert ["bankrotai.a", "bankrotai.b", "bankrotai.a"] in audit["internal_import_cycles_possible"]
    assert audit["dependencies"]["requirements_lock_packages"] == ["httpx"]
    assert audit["dependencies"]["requirements_dev_lock_packages"] == ["pytest"]
    assert audit["dependencies"]["web_dependencies"] == ["react"]
    assert audit["dependencies"]["web_dev_dependencies"] == ["vite"]


def test_real_repository_audit_smoke_is_read_only() -> None:
    audit = repository_audit(ROOT)
    assert audit["file_count"] > 0
    assert audit["tracked_bytes"] > 0
    assert audit["workflow_count"] > 0
    assert audit["git_status_unchanged"] is True
    assert audit["dependencies"]["lock_sha256"]["python_runtime"]


def test_regru_s3_pull_request_diagnostics_are_credentialless() -> None:
    workflow = (ROOT / ".github" / "workflows" / "regru-s3-diagnostics.yml").read_text(encoding="utf-8")
    pull_request_guard = 'if test "$GITHUB_EVENT_NAME" = "pull_request"; then'
    credential_check = 'elif test -n "$MAP_OBJECT_STORE_ACCESS_KEY" && test -n "$MAP_OBJECT_STORE_SECRET_KEY"; then'
    assert pull_request_guard in workflow
    assert credential_check in workflow
    assert workflow.index(pull_request_guard) < workflow.index(credential_check)
    assert 'echo "HAS_S3_CREDENTIALS=false" >> "$GITHUB_ENV"' in workflow
    # Security #1150 withholds secrets from every PR job environment, not
    # merely skipping the authenticated steps after secrets were loaded.
    for name in (
        "MAP_OBJECT_STORE_ACCESS_KEY",
        "MAP_OBJECT_STORE_SECRET_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
    ):
        env_line = next(
            line for line in workflow.splitlines()
            if line.strip().startswith(name + ":")
        )
        assert "github.event_name == 'workflow_dispatch'" in env_line
        assert "github.ref == 'refs/heads/main'" in env_line
        assert "|| ''" in env_line
