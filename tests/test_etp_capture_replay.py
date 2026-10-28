"""Offline regression tests for ETP Smart Capture replay. Synthetic fixtures only."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from bankrotai.connectors.registry.etp_capture_replay import (
    CaptureEvidenceError, inspect_capture,
)

ROOT = "TORGI_ETP_SMART_TEST/"


def fixture(path: Path, *, second: bool = True, duplicate: bool = False,
            source: str = "m-ets.ru", status: str = "catalogue_with_details"):
    row = {
        "site": source, "status": status, "distinct_pages": 1 if second else 0,
        "details_saved": 1, "api_bodies_saved": 0,
    }
    html_a = '<html><a href="https://m-ets.ru/243003-1">Дом 75 кв м</a></html>'
    html_b = (
        html_a if duplicate else
        '<html><a href="https://m-ets.ru/242999-1">Участок 800 кв м</a></html>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(ROOT + "summary.json", json.dumps({"sites": [row]}))
        z.writestr(ROOT + source + "/pages/002_catalogue_page_1/page.html", html_a)
        if second:
            z.writestr(ROOT + source + "/pages/003_catalogue_page_2/page.html", html_b)


def test_distinct_mets_pages_remain_pilot_not_production(tmp_path: Path):
    path = tmp_path / "capture.zip"
    fixture(path)
    report = inspect_capture(path)
    assert report["database_writes"] == 0
    assert report["archive_or_map_publication_allowed"] is False
    entry = report["sources"][0]
    assert entry["catalogue_candidates"] == 1
    assert entry["page_2_distinct_in_parser"] is True
    assert entry["production_ready"] is False
    assert entry["can_reconcile_missing"] is False


def test_page_two_identical_is_rejected(tmp_path: Path):
    path = tmp_path / "capture.zip"
    fixture(path, duplicate=True)
    entry = inspect_capture(path)["sources"][0]
    assert entry["page_2_distinct_in_parser"] is False
    assert "catalogue_page_2_is_identical" in entry["issues"]


def test_no_pagination_is_explicit(tmp_path: Path):
    path = tmp_path / "capture.zip"
    fixture(path, second=False)
    entry = inspect_capture(path)["sources"][0]
    assert entry["page_2_distinct_in_parser"] is False
    assert "page_2_not_captured" in entry["issues"]


def test_unknown_source_not_allowed(tmp_path: Path):
    path = tmp_path / "capture.zip"
    fixture(path, source="example.invalid")
    with pytest.raises(CaptureEvidenceError, match="Unrecognized"):
        inspect_capture(path)


def test_unverified_catalogue_not_promoted(tmp_path: Path):
    path = tmp_path / "capture.zip"
    fixture(path, status="navigation_error")
    row = inspect_capture(path)["sources"][0]
    assert row["catalogue_candidates"] == 0
    assert row["production_ready"] is False


def test_capture_never_echoes_raw_pages_or_personal_data(tmp_path: Path):
    path = tmp_path / "capture.zip"
    fixture(path)
    with zipfile.ZipFile(path, "a") as z:
        z.writestr(ROOT + "sensitive.txt", "password=PERSONAL-PRIVATE-DATA")
    report = inspect_capture(path)
    assert "PERSONAL-PRIVATE-DATA" not in json.dumps(report)
