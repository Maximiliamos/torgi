"""Property-level address checks; locality matches alone do not locate a lot."""
import re


def whole_locality_match(expected: str, observed: str) -> bool:
    expected = ' '.join(expected.casefold().replace('ё', 'е').split())
    observed = ' '.join(observed.casefold().replace('ё', 'е').split())
    return bool(expected and re.search(r'(?<!\w)' + re.escape(expected) + r'(?!\w)', observed))


def normalize_house(value: str) -> str:
    table = str.maketrans('авсехкмнорт', 'abcexkmhopt')
    return re.sub(r'\s+', '', value.casefold()).translate(table)


def property_number(address: str | None) -> str:
    value = address or ''
    number = r'[0-9]{1,5}[а-яa-z]?(?:\s*[/\-]\s*[0-9]{1,5}[а-яa-z]?)?'
    # A number following д. is a house, while д. followed by a name is a village.
    explicit = re.search(r'(?<!\w)(?:д\.?|дом|вл\.?|владение|уч\.?|участок|з/у)\s*(?:№\s*)?('
                         + number + r')(?![\w:])', value, re.IGNORECASE)
    if explicit:
        return normalize_house(explicit.group(1))
    # Standalone comma fields cover "улица ..., 12" and garage/plot numbers.
    bare = re.search(r'(?:^|[,;])\s*(' + number + r')\s*(?=[,;]|$)', value, re.IGNORECASE)
    return normalize_house(bare.group(1)) if bare else ''


def address_precision_rejection(query: str | None, matched: str | None) -> str | None:
    expected = property_number(query)
    observed = property_number(matched)
    if not expected:
        return 'property_locator_missing'
    if not observed:
        return 'matched_property_number_missing'
    if expected != observed:
        return 'property_number_mismatch'
    return None
