from datetime import datetime, timezone
from decimal import Decimal

import pytest

from bankrotai.db import SourceLot
from bankrotai.services.source_auction_facts import business_datetime_iso, source_auction_date, trusted_preview_raw, unverified_auction_fields
from bankrotai.services.source_auction_facts import source_price_checked_at


@pytest.mark.parametrize("proof_kind", ["legacy", "scoped", "receipt", "wrong_identity"])
def test_legacy_lotonline_dates_require_exact_card_or_primary_receipt(proof_kind):
    original = datetime(2026, 12, 1, 18)
    source = SourceLot(source_system="lot-online.ru", external_id="lot-online:111", application_deadline=original,
                       raw_data={"detail_enrichment_version": 3})
    if proof_kind in ("scoped", "wrong_identity"):
        source.raw_data = {"detail_enrichment_version": 4, "detail_identity_verified": True,
                           "detail_external_id": "lot-online:111" if proof_kind == "scoped" else "lot-online:222",
                           "detail_observed_fields": ["application_deadline"]}
    elif proof_kind == "receipt":
        source.raw_data = {"verified_source_fields": {"application_deadline": {
            "sha256": "a" * 64, "checked_at_utc": "2026-11-18T14:00:00+00:00"}}}
    proven = proof_kind in ("scoped", "receipt")
    assert source_auction_date(source, "application_deadline") == (original if proven else None)
    assert unverified_auction_fields(source) == ([] if proven else ["application_deadline"])
    assert source.application_deadline == original


def test_business_dates_and_utc_observations_have_distinct_time_conventions():
    assert business_datetime_iso(datetime(2026, 8, 23, 19, 30)) == "2026-08-23T19:30:00+03:00"
    assert business_datetime_iso(datetime(2026, 8, 23, 16, 30, tzinfo=timezone.utc)) == "2026-08-23T19:30:00+03:00"
    assert business_datetime_iso(None) is None


def test_other_source_business_dates_do_not_need_lotonline_scope_metadata():
    source = SourceLot(source_system="torgi.gov.ru", application_deadline=datetime(2026, 8, 23, 19, 30))
    assert source_auction_date(source, "application_deadline") == source.application_deadline
    assert unverified_auction_fields(source) == []


@pytest.mark.parametrize("version", [5, 7])
def test_russia_recommendation_metadata_requires_current_exact_detail_identity(version):
    original = datetime(2026, 12, 1, 18)
    source = SourceLot(source_system="torgi-russia.ru", external_id="torgi-russia:111", application_deadline=original,
                       raw_data={"detail_enrichment_version": version, "detail_identity_verified": True,
                                 "detail_external_id": "torgi-russia:111", "application_deadline": original.isoformat(),
                                 "gis_torgi_url": "https://torgi.gov.ru/lot/1", "image_urls": ["https://example.test/one.jpg"]})
    assert source_auction_date(source, "application_deadline") == (original if version == 7 else None)
    assert bool(trusted_preview_raw(source)) == (version == 7)
    assert source.raw_data["image_urls"] == ["https://example.test/one.jpg"]


def test_date_only_refresh_does_not_make_the_old_price_newly_verified():
    source = SourceLot(source_system='torgi.gov.ru',current_price=Decimal('100'),
        last_seen_at=datetime(2026,11,19),raw_data={'verified_source_fields':{
            'current_price':{'sha256':'a'*64,'checked_at_utc':'2026-11-18T12:00:00+00:00','value':'1E+2'},
            'auction_at':{'sha256':'b'*64,'checked_at_utc':'2026-11-19T00:00:00+00:00'}}})
    assert source_price_checked_at(source,'current_price',Decimal('100')) == datetime(2026,11,18,12,tzinfo=timezone.utc)
    source.current_price=Decimal('120')
    assert source_price_checked_at(source,'current_price',Decimal('120')) is None


@pytest.mark.parametrize('kind',['legacy_without_value','wrong_displayed_value','wrong_price_field'])
def test_price_freshness_requires_a_value_bound_proof_for_the_displayed_field(kind):
    proof={'sha256':'a'*64,'checked_at_utc':'2026-11-18T12:00:00+00:00','value':'1E+2'}
    if kind=='legacy_without_value':
        proof.pop('value')
    source=SourceLot(source_system='torgi.gov.ru',current_price=Decimal('100'),
        last_seen_at=datetime(2026,11,19),raw_data={'verified_source_fields':{'current_price':proof}})
    field='start_price' if kind=='wrong_price_field' else 'current_price'
    value=Decimal('120') if kind=='wrong_displayed_value' else Decimal('100')
    assert source_price_checked_at(source,field,value) is None
