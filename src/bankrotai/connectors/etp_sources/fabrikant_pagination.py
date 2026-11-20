"""Verified modern search controls and independently paginated procedure lots."""
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup


def modern_search_pagination(soup, *, page):
    paginator = soup.select_one('ul.rc-pagination[data-slot="pagination"]')
    if paginator is None:
        return None
    active = paginator.select_one('.rc-pagination-item-active')
    label = str(active.get('title') or active.get_text(' ', strip=True)) if active else ''
    if label != str(page):
        raise ValueError('Fabrikant search returned a different selected page')
    next_control = paginator.select_one('.rc-pagination-next')
    if next_control is None:
        raise ValueError('Fabrikant search next/terminal control is missing')
    disabled = (next_control.get('aria-disabled') == 'true'
                or 'rc-pagination-disabled' in next_control.get('class', [])
                or next_control.select_one('button[disabled]') is not None)
    visible = tuple(sorted({int(str(x.get('title'))) for x in paginator.select('.rc-pagination-item[title]')
                            if str(x.get('title')).isdigit()}))
    return not disabled, visible


def procedure_page_urls(content, *, procedure_url):
    """Only same-procedure numbered pagination can expand its lot identity scope."""
    base = urlparse(procedure_url)
    soup = BeautifulSoup(content, 'html.parser')
    urls = set()
    for anchor in soup.select('.pagination a[href]'):
        parsed = urlparse(urljoin(procedure_url, str(anchor.get('href') or '')))
        if (parsed.scheme != 'https' or parsed.hostname not in {'fabrikant.ru', 'www.fabrikant.ru'}
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.path != base.path):
            raise ValueError('Fabrikant procedure pagination changed identity or origin')
        query = parse_qs(parsed.query)
        values = query.get('page', [])
        if len(values) != 1 or not values[0].isdigit() or not 1 <= int(values[0]) <= 500:
            raise ValueError('Fabrikant procedure pagination is not a bounded numbered page')
        urls.add(urlunparse(base._replace(netloc='www.fabrikant.ru', fragment='')) if values[0] == '1'
                 else urlunparse(parsed._replace(netloc='www.fabrikant.ru', fragment='')))
    return sorted(urls)


def read_procedure_lot_urls(procedure_url, *, read_get, extract_lot_urls):
    pending = [procedure_url]
    visited, lots = set(), set()
    while pending:
        url = pending.pop(0)
        if url in visited:
            continue
        visited.add(url)
        if len(visited) > 500:
            raise ValueError('Fabrikant procedure page budget exceeded')
        response = read_get(url)
        lots.update(extract_lot_urls(response.content, base_url=response.url or url))
        pending.extend(u for u in procedure_page_urls(response.content, procedure_url=procedure_url)
                       if u not in visited)
    return sorted(lots)
