"""No-network and no-SQL regression coverage for five-page ETP proof."""
from types import SimpleNamespace
import hashlib

import pytest

from bankrotai.connectors.base import ConnectorPage
from bankrotai.services.etp_fivepage_readonly import (
    read_only_probe, verify_window,
)


def lot(external_id: str, source: str = "m-ets.ru"):
    return SimpleNamespace(
        external_id=external_id, source_system=source,
        category="land", cadastral_number="76:01:000101:1",
        auction_status="active", current_price=250000,
        start_price=None, lot_url="https://m-ets.ru/12345-1",
    )


def pages(*, ids=None, catalogue_ids=None, metadata_overrides=None, final_cursor=None):
    seq = ids or [[f"test-{n}"] for n in range(1, 6)]
    options = metadata_overrides or {}
    def fetch(filters, cursor):
        number = 1 if cursor is None else int(cursor.split(":")[0])
        row = seq[number - 1]
        catalogue = (catalogue_ids or seq)[number - 1]
        meta = {
            "complete_source_inventory": False, "safe_for_archival": False,
            "progress_current": number, "progress_total": 5,
            "catalogue_lots": len(catalogue), "details_rejected": 0,
            "catalogue_identity_fingerprints": [
                hashlib.sha256(item.encode("utf-8")).hexdigest()
                for item in catalogue
            ],
        }
        meta.update(options)
        return ConnectorPage(
            items=[lot(x) for x in row],
            next_cursor=(f"{number + 1}:deadbeefcafebabe" if number < 5
                         else final_cursor),
            metadata=meta,
        )
    return fetch


def test_five_distinct_pages_readonly_and_bounded():
    value = verify_window("m-ets.ru", pages())
    assert value == {
        "source": "m-ets.ru", "read_only": True, "no_database_writes": True,
        "pages_verified": 5, "catalogue_candidates_seen": 5,
        "unique_catalogue_id_fingerprints": 5,
        "verified_unique_property_details": 5, "details_rejected": 0,
        "complete_source_inventory": False, "safe_for_archival": False,
        "reconcile_missing": False, "map_publication_verified": False,
    }


def test_non_adjacent_duplicate_page_is_rejected_not_silently_counted():
    # Page 3 repeats page 1; old per-page cursors only caught adjacent repeats.
    with pytest.raises(RuntimeError, match="repeats an observed ID"):
        verify_window(
            "m-ets.ru", pages(ids=[
                ["lot-1"], ["lot-2"], ["lot-1"], ["lot-4"], ["lot-5"],
            ]),
        )


@pytest.mark.parametrize("metadata", [
    {"complete_source_inventory": True},
    {"safe_for_archival": True},
    {"progress_total": 2},
    {"progress_current": 0},
    {"catalogue_lots": 0},
    {"details_rejected": -1},
])
def test_forged_or_incomplete_catalogue_metadata_is_rejected(metadata):
    with pytest.raises(RuntimeError, match="metadata|evidenced"):
        verify_window("m-ets.ru", pages(metadata_overrides=metadata))


def test_early_stop_and_sixth_cursor_fail_closed():
    def truncated(f, cursor):
        result = pages()(f, cursor)
        if result.metadata["progress_current"] == 3:
            result.next_cursor = None
        return result
    with pytest.raises(RuntimeError, match="ended prematurely"):
        verify_window("m-ets.ru", truncated)
    with pytest.raises(RuntimeError, match="unbounded cursor"):
        verify_window("m-ets.ru", pages(final_cursor="6:deadbeefcafebabe"))


def test_only_approved_sources_and_one_to_five_pages():
    with pytest.raises(ValueError, match="Source not enabled"):
        verify_window("unapproved", pages())
    with pytest.raises(ValueError, match="bounded"):
        verify_window("m-ets.ru", pages(), pages=6)
    with pytest.raises(ValueError, match="Unapproved"):
        read_only_probe("unapproved")


def test_cross_source_or_non_property_card_fails_closed():
    def bad_source(f, cursor):
        result = pages()(f, cursor)
        if result.metadata["progress_current"] == 2:
            result.items[0].source_system = "cdtrf.ru"
        return result
    with pytest.raises(RuntimeError, match="repeated or unverified"):
        verify_window("m-ets.ru", bad_source)
    def bad_category(f, cursor):
        result = pages()(f, cursor)
        result.items[0].category = "vehicle"
        return result
    with pytest.raises(RuntimeError, match="repeated or unverified"):
        verify_window("m-ets.ru", bad_category)


def test_production_connections_not_executed_by_plain_validator():
    observed = []
    def fake(f, c):
        observed.append((f.source_id, c))
        return pages()(f, c)
    verify_window("m-ets.ru", fake)
    assert len(observed) == 5
    assert observed[0] == ("m-ets.ru", None)


def test_hidden_catalogue_duplicate_detected_outside_validated_details():
    # Page 1 and 3 share catalogue lot candidate 'hidden-repeat', which is
    # NOT among the two validated detail records on either page.
    seen = [
        ["a-1", "hidden-repeat", "extra-1"],
        ["b-1", "b-2", "extra-2"],
        ["c-1", "hidden-repeat", "extra-3"],
        ["d-1", "d-2", "extra-4"],
        ["e-1", "e-2", "extra-5"],
    ]
    accepted = [["a-1"], ["b-1"], ["c-1"], ["d-1"], ["e-1"]]
    with pytest.raises(RuntimeError, match="repeats an observed ID"):
        verify_window(
            "m-ets.ru", pages(ids=accepted, catalogue_ids=seen),
        )


def test_partial_bounded_window_counts_all_catalogue_not_just_detail_requests():
    candidates = [[f"page-{n}-candidate-{j}" for j in range(20)]
                  for n in range(1, 6)]
    accepted = [[page[0], page[1]] for page in candidates]
    result = verify_window(
        "m-ets.ru", pages(ids=accepted, catalogue_ids=candidates)
    )
    assert result["catalogue_candidates_seen"] == 100
    assert result["unique_catalogue_id_fingerprints"] == 100
    assert result["verified_unique_property_details"] == 10
    assert result["complete_source_inventory"] is False
    assert result["map_publication_verified"] is False


@pytest.mark.parametrize("metadata", [
    {"catalogue_identity_fingerprints": []},
    {"catalogue_identity_fingerprints": ["bad"]},
    {"catalogue_identity_fingerprints": None},
    {"catalogue_identity_fingerprints": [42]},
])
def test_catalogue_fingerprint_metadata_missing_or_forged(metadata):
    with pytest.raises(RuntimeError, match="identity fingerprint evidence"):
        verify_window("m-ets.ru", pages(metadata_overrides=metadata))


def test_connector_emits_public_candidate_fingerprints_without_raw_ids(monkeypatch):
    import bankrotai.connectors.registry.etp_incremental as mod
    monkeypatch.setattr(
        mod, "_get_mets_catalogue",
        lambda pilot, number: ('<html><a href="243003-1">Lot A</a>'
                               '<a href="243003-2">Lot B</a></html>'),
    )
    monkeypatch.setattr(
        mod, "parse_mets_lot",
        lambda html, url: lot(url.rsplit("/", 1)[-1]),
    )
    filters = mod.EtpIncrementalFilters(
        "m-ets.ru", max_detail_requests=1, max_pages=1
    )
    page = mod.incremental_mets_search(
        SimpleNamespace(_get=lambda url: "detail"), filters, None,
    )
    hashes = page.metadata["catalogue_identity_fingerprints"]
    assert len(hashes) == 2
    assert hashes == [
        hashlib.sha256(x.encode("utf-8")).hexdigest()
        for x in ["243003-1", "243003-2"]
    ]
    assert "243003-1" not in str(page.metadata)
    assert "243003-2" not in str(page.metadata)
