"""Regression for r6 -> r7 ended-auction map membership transition."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from bankrotai.db import Base, MapDataset, MapTile, ProcessedLot
from bankrotai.services.map_builder import _dimension_coverage_failures


def _coverage_check(
    *,
    old_revision: str = "r6",
    new_revision: str = "r7",
    surviving_active: int = 25,
) -> list[dict[str, object]]:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        previous = MapDataset(
            version=f"20261108T000000000000Z-{old_revision}-bundle-s3",
            status="ready",
            is_current=True,
        )
        candidate = MapDataset(
            version=f"20261109T000000000000Z-{new_revision}-bundle-s3",
            status="building",
        )
        session.add_all([previous, candidate])
        session.flush()

        legacy_source = [
            ProcessedLot(
                source="fixture",
                source_system="affected-source",
                external_id=f"affected-{i}",
                title="Land",
                category="land",
                region_code="76",
                auction_status=("completed" if i < 20 else "closed" if i < 35 else "active"),
            )
            for i in range(60)
        ]
        steady_source = [
            ProcessedLot(
                source="fixture",
                source_system="steady-source",
                external_id=f"steady-{i}",
                title="Land",
                category="land",
                region_code="50",
                auction_status="active",
            )
            for i in range(40)
        ]
        session.add_all(legacy_source + steady_source)
        session.flush()
        old_members = legacy_source + steady_source
        new_members = legacy_source[35:35 + surviving_active] + steady_source
        session.add_all([
            MapTile(
                dataset_id=previous.id,
                z=12, x=1, y=1,
                feature_count=len(old_members),
                etag="old",
                payload_json={"features": [{"kind": "lot", "id": lot.id} for lot in old_members]},
            ),
            MapTile(
                dataset_id=candidate.id,
                z=12, x=1, y=1,
                feature_count=len(new_members),
                etag="new",
                payload_json={"features": [{"kind": "lot", "id": lot.id} for lot in new_members]},
            ),
        ])
        session.commit()
        return _dimension_coverage_failures(
            session,
            current_dataset_id=previous.id,
            new_dataset_id=candidate.id,
            minimum_ratio=0.5,
        )


def test_r6_to_r7_excludes_only_ended_auctions_from_comparison_baseline():
    # A legitimate ended-auction removal must not look like a source outage.
    assert _coverage_check() == []


def test_r6_to_r7_still_rejects_unexplained_active_source_loss():
    failures = _coverage_check(surviving_active=8)
    assert any(
        failure["dimension"] == "source"
        and failure["value"] == "affected-source"
        and failure["previous"] == 25
        and failure["current"] == 8
        and failure["required"] == 13
        for failure in failures
    )


def test_same_revision_must_never_discount_ended_lots():
    failures = _coverage_check(old_revision="r7", new_revision="r7")
    assert any(
        failure["dimension"] == "source"
        and failure["value"] == "affected-source"
        and failure["previous"] == 60
        and failure["current"] == 25
        and failure["required"] == 30
        for failure in failures
    )


def test_unrelated_revision_transition_keeps_original_coverage_guard():
    failures = _coverage_check(old_revision="r5", new_revision="r7")
    assert any(
        failure["dimension"] == "source"
        and failure["value"] == "affected-source"
        for failure in failures
    )
