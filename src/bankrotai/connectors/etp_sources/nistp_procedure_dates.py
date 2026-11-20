"""Read labelled procedure dates without inheriting a sibling lot's schedule."""
import re
from datetime import datetime

from bs4 import BeautifulSoup

from bankrotai.connectors.etp_sources.nistp_detail_sections import NistpCardError

LABELS = {
    'дата начала представления заявок на участие': 'application_start_at',
    'дата окончания представления заявок на участие': 'application_deadline',
    'дата проведения': 'auction_at',
}


def procedure_dates(content):
    soup = BeautifulSoup(content, 'html.parser')
    values: dict[str, datetime] = {}
    if not re.search(r'время\s+московск|\bмск\b', soup.get_text(' ', strip=True), re.I):
        return values
    for cell in soup.select('td.label'):
        if any(re.fullmatch(r'table_lot_\d+', str(t.get('id') or '')) for t in cell.find_parents('table')):
            continue
        name = ' '.join(cell.get_text(' ', strip=True).casefold().split())
        key = LABELS.get(name)
        cells = cell.parent.find_all('td', recursive=False)
        if key is None or len(cells) != 2:
            continue
        raw = ' '.join(cells[1].get_text(' ', strip=True).split())
        parsed = None
        for fmt in ('%d.%m.%Y %H:%M:%S', '%d.%m.%Y %H:%M'):
            try:
                parsed = datetime.strptime(raw, fmt)
                break
            except ValueError:
                pass
        if parsed is None:
            continue
        if key in values and values[key] != parsed:
            raise NistpCardError('conflicting_procedure_dates')
        values[key] = parsed
    if values.get('application_start_at') and values.get('application_deadline'):
        if values['application_start_at'] > values['application_deadline']:
            raise NistpCardError('reversed_procedure_application_dates')
    return values
