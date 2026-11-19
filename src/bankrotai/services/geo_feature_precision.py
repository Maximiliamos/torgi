"""Property-level address checks; locality matches alone do not locate a lot."""
import re


def whole_locality_match(expected: str, observed: str) -> bool:
    expected = ' '.join(expected.casefold().replace('ё', 'е').split())
    observed = ' '.join(observed.casefold().replace('ё', 'е').split())
    return bool(expected and re.search(r'(?<!\w)' + re.escape(expected) + r'(?!\w)', observed))


def normalize_house(value: str) -> str:
    table = str.maketrans('авсехкмнорт', 'abcexkmhopt')
    value=re.sub(r'(?:корпус|корп\.?|к\.)\s*', 'к', value.casefold())
    value=re.sub(r'(?:строение|стр\.?|с\.)\s*', 'с', value)
    return re.sub(r'[\s,;]+', '', value).translate(table)


def property_number(address: str | None) -> str:
    value = address or ''
    number = r'[0-9]{1,5}[а-яa-z]?(?:\s*[/\-]\s*[0-9]{1,5}[а-яa-z]?)?'
    unit = r'(?:\s*,?\s*(?:корпус|корп\.?|[кk]\.?|строение|стр\.?|[сc]\.?)\s*[0-9]{1,4}[а-яa-z]?)*'
    token = number+unit
    # A number following д. is a house, while д. followed by a name is a village.
    explicit = re.search(r'(?<!\w)(?:д\.?|дом|вл\.?|владение|уч\.?|участок|з/у)\s*(?:№\s*)?('
                         + token + r')(?![\w:])', value, re.IGNORECASE)
    if explicit:
        return normalize_house(explicit.group(1))
    # Standalone comma fields cover "улица ..., 12" and garage/plot numbers.
    bare = re.search(r'(?:^|[,;])\s*(' + token + r')\s*(?=[,;]|$)', value, re.IGNORECASE)
    return normalize_house(bare.group(1)) if bare else ''


def property_street(address: str | None) -> tuple[str,str] | None:
    kinds={'улица':'улица','ул':'улица','проспект':'проспект','пр-т':'проспект','пр-кт':'проспект',
           'переулок':'переулок','пер':'переулок','шоссе':'шоссе','проезд':'проезд','пр-д':'проезд',
           'площадь':'площадь','пл':'площадь','набережная':'набережная','наб':'набережная',
           'бульвар':'бульвар','бул':'бульвар','тупик':'тупик','аллея':'аллея','тракт':'тракт'}
    kind='(?:'+'|'.join(re.escape(k) for k in sorted(kinds,key=len,reverse=True))+')'
    for part in re.split(r'[,;]',(address or '').casefold().replace('ё','е')):
        part=part.strip()
        match=re.fullmatch(r'('+kind+r')(?:\.\s*|\s+)(.+)',part)
        if match:
            label,name=match.groups()
            name=re.split(r'\s+(?:дом|д\.)\s*\d',name,maxsplit=1)[0]
        else:
            match=re.fullmatch(r'(.+?)\s+('+kind+r')\.?',part)
            if not match:continue
            name,label=match.groups()
        return kinds[label], ' '.join(name.split())
    return None


def address_precision_rejection(query: str | None, matched: str | None) -> str | None:
    expected = property_number(query)
    observed = property_number(matched)
    if not expected:
        return 'property_locator_missing'
    if not observed:
        return 'matched_property_number_missing'
    if expected != observed:
        return 'property_number_mismatch'
    street=property_street(query)
    if street and street!=property_street(matched):
        return 'property_street_mismatch'
    return None


def expected_locality_name(address: str | None) -> str:
    if not address:
        return ""
    full = list(re.finditer(
        r"(?:^|[,;]\s*)(?:город|деревня|село|пос[её]лок)\s+([^,;]+)",
        address, re.IGNORECASE,
    ))
    dotted = list(re.finditer(
        r"(?:^|[,;]\s*)(?:г|д|дер|с|п|пос|пгт)\.\s*(?![а-яё]\.)([А-ЯЁA-Z][^,;]*)",
        address, re.IGNORECASE,
    ))
    # Without a dot, 'с' is also an ordinary preposition in sale conditions.
    # Require a capitalized place and exclude explicit legal phrases. A nested
    # village after a city is common in the official administrative prefix.
    short = list(re.finditer(
        r"(?=(?:^|[,;]\s*|\s+)(?:г|д|дер|с|п|пос|пгт)(?:\.\s*|\s+)"
        r"(?![а-яё]\.)(?!одновременным\b|учетом\b|последующим\b|кадастровым\b|обременением\b)"
        r"((?-i:[А-ЯЁA-Z])[^,;]*))",
        address, re.IGNORECASE,
    ))
    candidates = [*full, *dotted, *short]
    if not candidates:
        return ""
    value = max(candidates, key=lambda item: item.start()).group(1)
    value = re.split(r"\bРоссийская\s+Федерация\b", value, maxsplit=1, flags=re.I)[0]
    value = re.split(r"\s+[А-ЯЁа-яё-]+\s+(?:область|обл\.?|край|район|республика)(?=\s|$)", value, maxsplit=1, flags=re.I)[0]
    value = re.split(r"\s+(?:в\s+районе|по\s+проезду|\d+\s*км\b|км\.)(?:\s|$)", value, maxsplit=1, flags=re.I)[0]
    return " ".join(value.strip(" .").split()).casefold()
