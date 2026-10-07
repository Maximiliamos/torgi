"""The API and Windows runtime must report the unified release version."""

import tomllib
from importlib.metadata import version
from pathlib import Path

from bankrotai import __version__


def test_runtime_distribution_version_matches_unified_project_metadata() -> None:
    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as stream:
        declared = tomllib.load(stream)["project"]["version"]
    assert version("bankrotai-finder") == declared
    assert __version__ == declared


def test_frozen_version_fallback_matches_release() -> None:
    root = Path(__file__).resolve().parents[1]
    package_init = (root / "src/bankrotai/__init__.py").read_text(encoding="utf-8")
    with (root / "pyproject.toml").open("rb") as stream:
        declared = tomllib.load(stream)["project"]["version"]
    assert f'__version__ = "{declared}"' in package_init
