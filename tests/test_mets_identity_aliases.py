from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from bankrotai.db import Base, LotNote, LotSyncRun, ProcessedLot, SourceLot
from bankrotai.domain import NormalizedLot
from bankrotai.logic import persist_lot
from bankrotai.services.batch_persistence import persist_changed_lots_batch
from bankrotai.services.duplicate_projection_policy import valid_duplicate_target
from bankrotai.services.mets_alias_repair import repair_mets_alias_links
from bankrotai.services.mets_identity_aliases import mets_native_identity, resolve_mets_alias_lots


def lot(identity):
    return NormalizedLot(external_id=identity, source="mets", source_system="m-ets.ru", title="Земельный участок",
                         description="Участок", category="land", region_slug="76", region_name="Ярославская область",
                         address="Ярославль, улица Победы, дом 12", cadastral_number="76:23:010101:1", vin=None,
                         area=100, start_price=1000, current_price=900, auction_status="active",
                         lot_url="https://m-ets.ru/lot/12345/1", source_url="https://m-ets.ru/lot/12345/1",
                         detail_level="public_detail", raw_data={})


def factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def make_pair(s):
    bare = persist_lot(s, lot("12345-1"))
    # Construct the pre-fix state; normal persistence must now prevent this.
    prefixed = ProcessedLot(source="mets", source_system="m-ets.ru", external_id="m-ets.ru:12345-1",
                             title=bare.title, category="land", auction_status="active")
    s.add(prefixed); s.flush()
    first_source = s.scalar(select(SourceLot).where(SourceLot.processed_lot_id==bare.id))
    second_source = SourceLot(canonical_lot_id=first_source.canonical_lot_id, processed_lot_id=prefixed.id,
                              source_system="m-ets.ru", external_id=prefixed.external_id, title=bare.title,
                              source_status="active", is_active=True, is_archived=False)
    checked = datetime.now(timezone.utc).isoformat()
    for source in [first_source, second_source]:
        source.raw_data = {"primary_provider_external_id": "12345-1", "stored_id_spelling_verified": True,
                           "verified_source_fields": {"title": {"sha256": "a"*64, "checked_at_utc": checked, "value": bare.title}}}
    s.add(second_source); s.commit()
    return bare, prefixed


def commit_pair(s, ids):
    plan = repair_mets_alias_links(s, lot_ids=ids)
    result = repair_mets_alias_links(s, lot_ids=ids, apply=True,
        expected_states={i["lot_id"]:i["expected_state"] for i in plan["items"]},
        expected_selection_sha256=plan["selection_sha256"])
    s.commit()
    return result


@pytest.mark.parametrize("identity", ["12345", "12345:1", "12345-2-extra", "m-ets.ru:12345", "00001-1", "https://m-ets.ru/12345-1"])
def test_procedure_or_ambiguous_strings_are_not_identity_aliases(identity):
    assert mets_native_identity("m-ets.ru", identity) is None


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("original", ["12345-1", "m-ets.ru:12345-1"])
def test_future_import_uses_lone_existing_spelling_and_preserves_grade_and_financial_inputs(batch, original):
    with factory()() as s:
        if original.startswith("m-ets"):
            p = ProcessedLot(source="mets", source_system="m-ets.ru", external_id=original,
                             title="Old", category="land", auction_status="active")
            s.add(p); s.flush()
            persist_lot(s, lot(original))
        else:
            p = persist_lot(s, lot(original))
        p.review_status, p.rating, p.monthly_fixed_rent = "maybe", 8.5, 2345
        s.commit(); original_id=p.id
        incoming = replace(lot("12345-1" if original.startswith("m-ets") else "m-ets.ru:12345-1"),
                           title="Updated primary title", monthly_fixed_rent=999999)
        if batch:
            s.add(LotSyncRun(id="refresh", trigger_type="test", status="running")); s.flush()
            persist_changed_lots_batch(s, [incoming], "refresh")
        else:
            persist_lot(s, incoming)
        s.commit(); s.refresh(p)
        assert s.scalar(select(func.count()).select_from(ProcessedLot)) == 1
        assert s.scalar(select(func.count()).select_from(SourceLot)) == 1
        assert p.id==original_id and p.external_id==original
        assert p.title==incoming.title and p.review_status=="maybe" and p.rating==8.5
        assert float(p.monthly_fixed_rent)==2345


def test_unlinked_existing_pair_requires_audited_repair():
    with factory()() as s:
        bare, prefixed=make_pair(s)
        assert resolve_mets_alias_lots(s, [lot(prefixed.external_id)])[0].external_id==prefixed.external_id
        assert bare.duplicate_of_id is None and prefixed.duplicate_of_id is None


@pytest.mark.parametrize("protected", ["bare", "prefixed", "note"])
def test_bounded_repair_retains_both_records_and_existing_user_state_without_transfers(protected):
    with factory()() as s:
        bare, prefixed=make_pair(s)
        primary=bare if protected=="bare" else prefixed
        if protected=="note":
            s.add(LotNote(lot_id=prefixed.id, user_id=1, content="My note"))
        else:
            primary.review_status, primary.rating="approved", 9.25
        s.commit()
        source_ids=list(s.scalars(select(SourceLot.id).order_by(SourceLot.id)))
        result=commit_pair(s, [bare.id,prefixed.id])
        secondary=prefixed if primary.id==bare.id else bare
        assert result["repairable"]==1 and result["deleted_lots"]==0
        assert secondary.duplicate_of_id==primary.id and primary.duplicate_of_id is None
        assert s.scalar(select(func.count()).select_from(ProcessedLot))==2
        assert list(s.scalars(select(SourceLot.id).order_by(SourceLot.id)))==source_ids
        assert valid_duplicate_target(secondary,primary)
        assert secondary.review_status is None and (protected=="note" or primary.review_status=="approved")
        if protected=="note":
            assert s.scalar(select(LotNote)).lot_id==prefixed.id
        resolved=resolve_mets_alias_lots(s,[lot(secondary.external_id)])[0]
        assert resolved.external_id==primary.external_id


@pytest.mark.parametrize("invalid", ["both_reviewed", "expired", "different_sha", "wrong_provider_id", "title_changed", "foreign_source_binding", "dependent_child"])
def test_unproven_pair_or_conflicting_user_state_is_not_linked(invalid):
    with factory()() as s:
        bare,prefixed=make_pair(s)
        source=s.scalar(select(SourceLot).where(SourceLot.processed_lot_id==prefixed.id))
        raw=dict(source.raw_data);proof=dict(raw['verified_source_fields']['title'])
        if invalid=="both_reviewed":
            bare.review_status,prefixed.review_status="approved","rejected"
        elif invalid=="expired":
            proof['checked_at_utc']=(datetime.now(timezone.utc)-timedelta(hours=25)).isoformat()
        elif invalid=="different_sha":
            proof['sha256']='b'*64
        elif invalid=="wrong_provider_id":
            raw['primary_provider_external_id']='54321-1'
        elif invalid=="title_changed":
            source.title='Another title'
        elif invalid=="foreign_source_binding":
            foreign=ProcessedLot(source_system="other", external_id="foreign", title="Other sale", category="land")
            s.add(foreign);s.flush()
            source.processed_lot_id=foreign.id
        else:
            s.add(ProcessedLot(source_system='other',external_id='child',title='Child',category='land',duplicate_of_id=prefixed.id))
        raw['verified_source_fields']={'title':proof};source.raw_data=raw;s.commit()
        result=commit_pair(s,[bare.id,prefixed.id])
        assert result['repairable']==0 and result['blocked']==1
        assert bare.duplicate_of_id is None and prefixed.duplicate_of_id is None


def test_concurrent_review_change_invalidates_preview_before_linking():
    with factory()() as s:
        bare,prefixed=make_pair(s);ids=[bare.id,prefixed.id]
        plan=repair_mets_alias_links(s,lot_ids=ids)
        prefixed.review_status='rejected';s.commit()
        with pytest.raises(ValueError,match='stale'):
            repair_mets_alias_links(s,lot_ids=ids,apply=True,
                expected_states={i['lot_id']:i['expected_state'] for i in plan['items']},
                expected_selection_sha256=plan['selection_sha256'])
        assert bare.duplicate_of_id is None and prefixed.duplicate_of_id is None


def test_other_lot_in_same_procedure_and_shared_cadastre_is_not_an_alias():
    parent=SimpleNamespace(id=1,source_system='m-ets.ru',external_id='12345-1',duplicate_of_id=None)
    child=SimpleNamespace(id=2,source_system='m-ets.ru',external_id='m-ets.ru:12345-2',duplicate_of_id=None)
    assert not valid_duplicate_target(child,parent)


@pytest.mark.parametrize("batch", [False, True])
def test_deleted_publication_is_not_revived_under_other_spelling(batch):
    from bankrotai.services.source_lot_exclusions import register_exclusion, SourceLotExcludedError
    with factory()() as s:
        register_exclusion(s, source_system="m-ets.ru", external_id="m-ets.ru:12345-1", reason="user deletion")
        s.commit()
        with pytest.raises(SourceLotExcludedError):
            if batch:
                persist_changed_lots_batch(s, [lot("12345-1")], "never-written")
            else:
                persist_lot(s, lot("12345-1"))
        assert s.scalar(select(func.count()).select_from(ProcessedLot))==0


def test_ingestion_presence_tracks_both_spellings_without_creating_an_alias_record():
    from bankrotai.services.ingestion import NationwideIngestionService
    from bankrotai.services.ingestion_models import SourceSyncResult
    f=factory()
    with f() as s:
        p=ProcessedLot(source_system="m-ets.ru", external_id="m-ets.ru:12345-1", title="Old", category="land")
        s.add(p);s.flush();persist_lot(s,lot(p.external_id));s.commit()
        s.add(LotSyncRun(id="presence", trigger_type="test", status="running"));s.flush()
        service=NationwideIngestionService(f)
        result=SourceSyncResult(source_system="m-ets.ru")
        accepted=resolve_mets_alias_lots(s,[lot("12345-1")])
        service._persist_page_legacy(s,"presence",result,accepted)
        s.commit()
        assert result.seen_external_ids=={"12345-1","m-ets.ru:12345-1"}
        assert s.scalar(select(func.count()).select_from(ProcessedLot))==1
        assert s.scalar(select(func.count()).select_from(SourceLot))==1


@pytest.mark.parametrize("batch", [False, True])
def test_rejected_fresh_alias_marks_the_existing_primary_ineligible_without_losing_its_grade(batch):
    import asyncio
    from bankrotai.services.ingestion import NationwideIngestionService
    from bankrotai.services.ingestion_models import SourceSyncResult
    f=factory()
    with f() as s:
        p=ProcessedLot(source_system="m-ets.ru",external_id="m-ets.ru:12345-1",title="Land",category="land")
        s.add(p);s.flush();persist_lot(s,lot(p.external_id));p.review_status="approved";s.commit();pid=p.id
        s.add(LotSyncRun(id="reject",trigger_type="test",status="running"));s.commit()
    service=NationwideIngestionService(f,use_gis_batch_persistence=batch)
    result=SourceSyncResult(source_system="m-ets.ru")
    rejected=replace(lot("12345-1"),title="Автомобиль Ford",category="car")
    asyncio.run(service._persist_page("reject",result,[rejected]))
    with f() as s:
        source=s.scalar(select(SourceLot))
        assert source.processed_lot_id==pid and source.raw_data["source_sale_eligibility"] is False
        assert s.get(ProcessedLot,pid).review_status=="approved"
        assert s.scalar(select(func.count()).select_from(ProcessedLot))==1
    assert result.seen_external_ids=={"12345-1","m-ets.ru:12345-1"}
