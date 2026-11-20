from datetime import datetime

import pytest

from bankrotai.connectors.etp_sources.nistp_procedure_dates import procedure_dates


def test_only_labelled_moscow_procedure_dates_are_shared_with_individual_lots():
    page='''<span>время московское</span><table class="data">
    <tr><td class="label">Дата начала представления заявок на участие</td><td>20.11.2026 00:00:00</td></tr>
    <tr><td class="label">Дата окончания представления заявок на участие</td><td>25.12.2026 18:30:00</td></tr>
    <tr><td class="label">Дата проведения</td><td>29.12.2026 15:00:00</td></tr></table>
    <table id="table_lot_2"><tr><td class="label">Дата проведения</td><td>01.01.1999 12:00:00</td></tr></table>'''
    assert procedure_dates(page)=={'application_start_at':datetime(2026,11,20),
        'application_deadline':datetime(2026,12,25,18,30),'auction_at':datetime(2026,12,29,15)}
    assert procedure_dates(page.replace('время московское','местное время'))=={}


def test_conflicting_procedure_dates_are_not_selected_from_nearby_rows():
    page='''время московское<table><tr><td class="label">Дата проведения</td><td>29.12.2026 15:00</td></tr>
    <tr><td class="label">Дата проведения</td><td>30.12.2026 15:00</td></tr></table>'''
    with pytest.raises(ValueError,match='conflicting'):
        procedure_dates(page)
