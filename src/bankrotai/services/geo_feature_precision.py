"""Property-level address checks; locality matches alone do not locate a lot."""
import re


def whole_locality_match(expected: str, observed: str) -> bool:
    expected = ' '.join(expected.casefold().replace('ё', 'е').split())
    observed = ' '.join(observed.casefold().replace('ё', 'е').split())
    return bool(expected and re.search(r'(?<!\w)' + re.escape(expected) + r'(?!\w)', observed))


def normalize_house(value: str) -> str:
    table = str.maketrans('авсехкмнорт', 'abcexkmhopt')
    value=re.sub(r'(?:корпус|корп\.?|к\.)\s*', 'к', value.casefold())
    value=re.sub(r'(?:строение|стр\.?)\s*', 'с', value)
    return re.sub(r'[\s,;]+', '', value).translate(table)


def property_number(address: str | None) -> str:
    value = address or ''
    number = r'[0-9]{1,5}[а-яa-z]?(?:\s*[/\-]\s*[0-9]{1,5}[а-яa-z]?)?'
    unit = r'(?:\s*,?\s*(?:корпус|корп\.?|к\.?|строение|стр\.?)\s*[0-9]{1,4}[а-яa-z]?)*'
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
