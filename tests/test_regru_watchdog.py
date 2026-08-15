from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
WATCHDOG = ROOT / "scripts" / "regru-api-watchdog.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture()
def watchdog_env(tmp_path: Path) -> dict[str, str]:
    if os.name == "nt" or shutil.which("bash") is None:
        pytest.skip("watchdog regression tests require bash")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "curl",
        """#!/usr/bin/env bash
if [[ "${*: -1}" == *health/live ]]; then
  exit "${FAKE_LIVE_EXIT:-0}"
fi
exit "${FAKE_READY_EXIT:-0}"
""",
    )
    _write_executable(fake_bin / "logger", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        fake_bin / "docker",
        """#!/usr/bin/env bash
if [[ "$1 $2 $3" == "container inspect bankrotai-cloudflared" ]]; then
  exit "${FAKE_TUNNEL_EXISTS:-0}"
fi
if [[ "$1" == restart ]]; then
  printf '%s\\n' "$2" >> "$WATCHDOG_DOCKER_LOG"
fi
exit 0
""",
    )
    return {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "WATCHDOG_STATE_FILE": str(tmp_path / "failures"),
        "WATCHDOG_RESTART_FILE": str(tmp_path / "last-restart"),
        "WATCHDOG_LOCK_FILE": str(tmp_path / "lock"),
        "WATCHDOG_DOCKER_LOG": str(tmp_path / "docker.log"),
        "WATCHDOG_NOW_EPOCH": "1000",
        "WATCHDOG_RESTART_COOLDOWN_SECONDS": "300",
    }


def _run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(WATCHDOG)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_watchdog_ignores_healthy_api_and_readiness_only_failure(watchdog_env: dict[str, str]) -> None:
    state = Path(watchdog_env["WATCHDOG_STATE_FILE"])
    state.write_text("2\n", encoding="utf-8")
    watchdog_env["FAKE_READY_EXIT"] = "1"

    assert _run(watchdog_env).returncode == 0
    assert not state.exists()
    assert not Path(watchdog_env["WATCHDOG_DOCKER_LOG"]).exists()


def test_watchdog_requires_three_liveness_failures_and_restarts_once(watchdog_env: dict[str, str]) -> None:
    watchdog_env["FAKE_LIVE_EXIT"] = "1"
    state = Path(watchdog_env["WATCHDOG_STATE_FILE"])
    docker_log = Path(watchdog_env["WATCHDOG_DOCKER_LOG"])

    assert _run(watchdog_env).returncode == 0
    assert state.read_text(encoding="utf-8").strip() == "1"
    assert _run(watchdog_env).returncode == 0
    assert state.read_text(encoding="utf-8").strip() == "2"
    assert not docker_log.exists()

    assert _run(watchdog_env).returncode == 0
    assert docker_log.read_text(encoding="utf-8").splitlines() == [
        "bankrotai-api",
        "bankrotai-cloudflared",
    ]
    assert not state.exists()


def test_watchdog_restart_cooldown_prevents_restart_loop(watchdog_env: dict[str, str]) -> None:
    watchdog_env["FAKE_LIVE_EXIT"] = "1"
    Path(watchdog_env["WATCHDOG_STATE_FILE"]).write_text("2\n", encoding="utf-8")
    Path(watchdog_env["WATCHDOG_RESTART_FILE"]).write_text("900\n", encoding="utf-8")

    assert _run(watchdog_env).returncode == 0
    assert not Path(watchdog_env["WATCHDOG_DOCKER_LOG"]).exists()
    assert Path(watchdog_env["WATCHDOG_STATE_FILE"]).read_text(encoding="utf-8").strip() == "3"
