"""Shared classification for scoped ETP lot descriptions."""

def classify_etp_category(value: str) -> str:
    from bankrotai.services.asset_category import explicit_nonproperty_category
    explicit = explicit_nonproperty_category(value)
    if explicit:
        return {"car": "vehicle", "receivable": "receivables"}.get(explicit, explicit)
    lowered = value.lower()
    if any(word in lowered for word in ("квартир", "комнат", "жилой дом", "недвижим", "помещен", "здани", "гараж", "сооружен")):
        return "real_estate"
    if any(word in lowered for word in ("земельн", "участок", "кадастров")):
        return "land"
    if any(word in lowered for word in ("автомоб", "транспорт", "vin", "прицеп", "снегоход")):
        return "vehicle"
    if any(word in lowered for word in ("право требован", "дебитор")):
        return "receivables"
    return "other"
