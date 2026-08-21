from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RegionDefinition:
    code: str
    name: str
    aliases: tuple[str, ...] = ()
    gis_torgi_code: str | None = None
    tbankrot_code: str | None = None
    torgi_russia_code: str | None = None
    lot_online_code: str | None = None


# Canonical codes use the primary two-digit subject codes from Appendix 1 to
# Ministry of Internal Affairs Order No. 766. Source-specific identifiers are
# deliberately separate and may be populated only from verified source contracts.
REGION_DIRECTORY: tuple[RegionDefinition, ...] = (
    RegionDefinition("01", "Республика Адыгея", ("Адыгея", "Респ Адыгея")),
    RegionDefinition("02", "Республика Башкортостан", ("Башкортостан", "Респ Башкортостан")),
    RegionDefinition("03", "Республика Бурятия", ("Бурятия", "Респ Бурятия")),
    RegionDefinition("04", "Республика Алтай", ("Респ Алтай",)),
    RegionDefinition("05", "Республика Дагестан", ("Дагестан", "Респ Дагестан")),
    RegionDefinition("06", "Республика Ингушетия", ("Ингушетия", "Респ Ингушетия")),
    RegionDefinition("07", "Кабардино-Балкарская Республика", ("Кабардино-Балкария", "КБР")),
    RegionDefinition("08", "Республика Калмыкия", ("Калмыкия", "Респ Калмыкия")),
    RegionDefinition("09", "Карачаево-Черкесская Республика", ("Карачаево-Черкесия", "КЧР")),
    RegionDefinition("10", "Республика Карелия", ("Карелия", "Респ Карелия")),
    RegionDefinition("11", "Республика Коми", ("Коми", "Респ Коми")),
    RegionDefinition("12", "Республика Марий Эл", ("Марий Эл", "Респ Марий Эл")),
    RegionDefinition("13", "Республика Мордовия", ("Мордовия", "Респ Мордовия")),
    RegionDefinition("14", "Республика Саха (Якутия)", ("Якутия", "Республика Саха", "Саха Якутия")),
    RegionDefinition("15", "Республика Северная Осетия-Алания", ("Северная Осетия", "Северная Осетия-Алания")),
    RegionDefinition("16", "Республика Татарстан", ("Татарстан", "Респ Татарстан")),
    RegionDefinition("17", "Республика Тыва", ("Тыва", "Тува", "Республика Тува")),
    RegionDefinition("18", "Удмуртская Республика", ("Удмуртия",)),
    RegionDefinition("19", "Республика Хакасия", ("Хакасия", "Респ Хакасия")),
    RegionDefinition("20", "Чеченская Республика", ("Чечня",)),
    RegionDefinition("21", "Чувашская Республика", ("Чувашия", "Чувашская Республика — Чувашия")),
    RegionDefinition("22", "Алтайский край"),
    RegionDefinition("23", "Краснодарский край"),
    RegionDefinition("24", "Красноярский край"),
    RegionDefinition("25", "Приморский край"),
    RegionDefinition("26", "Ставропольский край"),
    RegionDefinition("27", "Хабаровский край"),
    RegionDefinition("28", "Амурская область", ("Амурская обл",)),
    RegionDefinition("29", "Архангельская область", ("Архангельская обл",)),
    RegionDefinition("30", "Астраханская область", ("Астраханская обл",)),
    RegionDefinition("31", "Белгородская область", ("Белгородская обл",)),
    RegionDefinition("32", "Брянская область", ("Брянская обл",)),
    RegionDefinition("33", "Владимирская область", ("Владимирская обл",)),
    RegionDefinition("34", "Волгоградская область", ("Волгоградская обл",)),
    RegionDefinition("35", "Вологодская область", ("Вологодская обл",)),
    RegionDefinition("36", "Воронежская область", ("Воронежская обл",)),
    RegionDefinition("37", "Ивановская область", ("Ивановская обл",)),
    RegionDefinition("38", "Иркутская область", ("Иркутская обл",)),
    RegionDefinition("39", "Калининградская область", ("Калининградская обл",)),
    RegionDefinition("40", "Калужская область", ("Калужская обл",)),
    RegionDefinition("41", "Камчатский край"),
    RegionDefinition("42", "Кемеровская область", ("Кемеровская обл", "Кузбасс", "Кемеровская область - Кузбасс")),
    RegionDefinition("43", "Кировская область", ("Кировская обл",)),
    RegionDefinition("44", "Костромская область", ("Костромская обл",)),
    RegionDefinition("45", "Курганская область", ("Курганская обл",)),
    RegionDefinition("46", "Курская область", ("Курская обл",)),
    RegionDefinition("47", "Ленинградская область", ("Ленинградская обл",)),
    RegionDefinition("48", "Липецкая область", ("Липецкая обл",)),
    RegionDefinition("49", "Магаданская область", ("Магаданская обл",)),
    RegionDefinition("50", "Московская область", ("Московская обл", "Подмосковье")),
    RegionDefinition("51", "Мурманская область", ("Мурманская обл",)),
    RegionDefinition("52", "Нижегородская область", ("Нижегородская обл",)),
    RegionDefinition("53", "Новгородская область", ("Новгородская обл",)),
    RegionDefinition("54", "Новосибирская область", ("Новосибирская обл",)),
    RegionDefinition("55", "Омская область", ("Омская обл",)),
    RegionDefinition("56", "Оренбургская область", ("Оренбургская обл",)),
    RegionDefinition("57", "Орловская область", ("Орловская обл",)),
    RegionDefinition("58", "Пензенская область", ("Пензенская обл",)),
    RegionDefinition("59", "Пермский край"),
    RegionDefinition("60", "Псковская область", ("Псковская обл",)),
    RegionDefinition("61", "Ростовская область", ("Ростовская обл",)),
    RegionDefinition("62", "Рязанская область", ("Рязанская обл",)),
    RegionDefinition("63", "Самарская область", ("Самарская обл",)),
    RegionDefinition("64", "Саратовская область", ("Саратовская обл",)),
    RegionDefinition("65", "Сахалинская область", ("Сахалинская обл",)),
    RegionDefinition("66", "Свердловская область", ("Свердловская обл",)),
    RegionDefinition("67", "Смоленская область", ("Смоленская обл",)),
    RegionDefinition("68", "Тамбовская область", ("Тамбовская обл",)),
    RegionDefinition("69", "Тверская область", ("Тверская обл",)),
    RegionDefinition("70", "Томская область", ("Томская обл",)),
    RegionDefinition("71", "Тульская область", ("Тульская обл",)),
    RegionDefinition("72", "Тюменская область", ("Тюменская обл",)),
    RegionDefinition("73", "Ульяновская область", ("Ульяновская обл",)),
    RegionDefinition("74", "Челябинская область", ("Челябинская обл",)),
    RegionDefinition("75", "Забайкальский край"),
    RegionDefinition("76", "Ярославская область", ("Ярославская обл",)),
    RegionDefinition("77", "г. Москва", ("Москва", "г Москва", "город Москва")),
    RegionDefinition("78", "г. Санкт-Петербург", ("Санкт-Петербург", "СПб", "г Санкт-Петербург")),
    RegionDefinition("79", "Еврейская автономная область", ("ЕАО", "Еврейская автономная обл")),
    RegionDefinition("80", "Донецкая Народная Республика", ("ДНР", "Донецкая народная Республика")),
    RegionDefinition("81", "Луганская народная Республика", ("ЛНР", "Луганская Народная Республика")),
    RegionDefinition("82", "Республика Крым", ("Крым", "Респ Крым")),
    RegionDefinition("83", "Ненецкий автономный округ", ("НАО",)),
    RegionDefinition("84", "Херсонская область", ("Херсонская обл",)),
    RegionDefinition("85", "Запорожская область", ("Запорожская обл",)),
    RegionDefinition("86", "Ханты-Мансийский автономный округ", ("ХМАО", "Ханты-Мансийский АО", "Югра")),
    RegionDefinition("87", "Чукотский автономный округ", ("ЧАО", "Чукотский АО")),
    RegionDefinition("89", "Ямало-Ненецкий автономный округ", ("ЯНАО", "Ямало-Ненецкий АО")),
    RegionDefinition("92", "г. Севастополь", ("Севастополь", "г Севастополь")),
)


def _key(value: str) -> str:
    normalized = value.casefold().replace("ё", "е")
    normalized = re.sub(r"[.(),—–-]+", " ", normalized)
    return " ".join(normalized.split())


REGIONS_BY_CODE = {region.code: region for region in REGION_DIRECTORY}
_REGION_CODES_BY_ALIAS = {
    _key(alias): region.code
    for region in REGION_DIRECTORY
    for alias in (region.name, *region.aliases)
}


def normalize_region_code(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if candidate.isdigit():
        code = candidate.zfill(2)
        return code if code in REGIONS_BY_CODE else None
    return _REGION_CODES_BY_ALIAS.get(_key(candidate))


def region_label(code: str) -> str:
    region = REGIONS_BY_CODE[code]
    return f"{region.code} — {region.name}"
