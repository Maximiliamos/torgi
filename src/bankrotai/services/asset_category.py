"""Classify the offered object before incidental words in its description."""
import re

_VEHICLE = re.compile(
    r"\b(?:автомобил(?:ь|я|ю|ем|и|ей|ями|ях)|автобус\w*|автокран\w*|"
    r"мотоцикл\w*|полуприцеп\w*|прицеп\w*|vin)\b", re.IGNORECASE,
)
_BRAND = re.compile(r"^\s*(?:volkswagen|ford|уаз|камаз|маз|газель)\b", re.IGNORECASE)
_CLAIM = re.compile(
    r"^\s*(?:(?:продажа|уступка|реализация)\s+)?(?:прав[оа]\s+требован|"
    r"дебиторск\w*\s+задолженн|денежн\w*\s+требован)", re.IGNORECASE,
)
_PROPERTY_NOUN = re.compile(
    r"\b(?:квартир\w*|помещени\w*|здани\w*|склад\w*|цех\w*|земельн\w*|"
    r"участ(?:ок|ка|ку|ком|ке|ки|ков|ками|ках)|гараж\w*|коттедж\w*|комнат\w*|"
    r"машино[- ]?мест\w*|жилой\s+дом|дом\s+жилой)\b", re.IGNORECASE,
)
_EQUIPMENT = re.compile(
    r"^\s*(?:комплект\s+)?(?:оборудовани(?:е|я|ю|ем|и)|станок|станки|"
    r"экскаватор\w*|погрузчик\w*|трактор\w*|бульдозер\w*|оргтехник\w*|мебель)\b",
    re.IGNORECASE,
)


def _property(text: str) -> str | None:
    land = "земельн" in text or bool(re.search(r"\b(?:зу|участ(?:ок|ка|ку|ком|ке|ки|ков|ками|ках))\b", text))
    if "квартир" in text:
        return "apartment"
    if re.search(r"\bжил\w*\s+помещени", text):
        return "living"
    if "помещени" in text:
        return "commercial_room"
    if any(word in text for word in ("здание", "склад", "цех")):
        return "commercial_building_with_land" if land else "commercial_building"
    if any(word in text for word in ("жилой дом", "дом жилой", "коттедж")):
        return "house"
    if land:
        return "land"
    if "машино-место" in text or "машиноместо" in text or "гараж" in text:
        return "parking"
    if re.search(r"\bкомнат\w*", text):
        return "real_estate"
    if "незаверш" in text:
        return "unfinished"
    if "комплекс" in text:
        return "complex"
    if "недвиж" in text:
        return "real_estate"
    return None


def explicit_nonproperty_category(title: str) -> str | None:
    if _CLAIM.search(title):
        return "receivable"
    if _EQUIPMENT.search(title):
        return "equipment"
    vehicle = _VEHICLE.search(title) or _BRAND.search(title)
    property_noun = _PROPERTY_NOUN.search(title)
    if vehicle and (property_noun is None or vehicle.start() < property_noun.start()):
        return "car"
    return None


def classify_category(title: str, description: str) -> str:
    title = (title or "").casefold().replace("ё", "е")
    description = (description or "").casefold().replace("ё", "е")
    explicit = explicit_nonproperty_category(title)
    if explicit:
        return explicit
    category = _property(title)
    if category:
        return category
    if _CLAIM.search(description):
        return "receivable"
    text = title + " " + description
    if _VEHICLE.search(text) or _BRAND.search(description):
        return "car"
    category = _property(text)
    if category:
        return category
    if any(token in text for token in ("автомоб", "vin", "грузов", "транспорт", "авто", "прицеп", "легков")):
        return "car"
    if "дебитор" in text or "право требования" in text:
        return "receivable"
    return "other"
