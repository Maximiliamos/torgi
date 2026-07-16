import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class ExtractionResult:
    value: float | int | str | None
    source_fragment: str | None
    rule_id: str
    confidence: str
    warnings: list[str] = field(default_factory=list)


_NUMBER = r"\d{1,3}(?:[\s.,]\d{3})*(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
_PRICE_LABELS = (
    ("current_price", r"(?:текущая|актуальная)\s+цена"),
    ("start_price", r"(?:начальная|стартовая)\s+цена"),
    ("sale_price", r"(?:цена\s+продажи|стоимость\s+лота|цена\s+лота)"),
    ("generic_price", r"(?:цена|стоимость)"),
)
_EXCLUDED_PRICE_CONTEXT = ("задат", "шаг аукциона", "долг", "задолж", "площад")


def _normalize_text(text: str) -> str:
    clean = re.sub(r"[\xa0\u202f\u2009]", " ", text)
    return re.sub(r"\s+", " ", clean).strip()


def extract_price_result(text: str) -> ExtractionResult:
    if not text:
        return ExtractionResult(None, None, "price.empty", "none", ["Source text is empty."])

    clean = _normalize_text(text)
    currency = r"(?:₽|руб(?:\.|лей|ля)?)"
    for rule_id, label in _PRICE_LABELS:
        pattern = rf"{label}[^.;\n]{{0,40}}?({_NUMBER})\s*{currency}"
        match = re.search(pattern, clean, re.IGNORECASE)
        if match:
            value = _to_float_num(match.group(1))
            if value and value > 0:
                confidence = "high" if rule_id in {"current_price", "start_price"} else "medium"
                return ExtractionResult(value, match.group(0), f"price.{rule_id}", confidence)

    candidates: list[tuple[float, str]] = []
    for match in re.finditer(rf"({_NUMBER})\s*{currency}", clean, re.IGNORECASE):
        context = clean[max(0, match.start() - 45):min(len(clean), match.end() + 20)].lower()
        if any(marker in context for marker in _EXCLUDED_PRICE_CONTEXT):
            continue
        value = _to_float_num(match.group(1))
        if value and value > 0:
            candidates.append((value, match.group(0)))

    if len(candidates) == 1:
        value, fragment = candidates[0]
        return ExtractionResult(
            value,
            fragment,
            "price.single_currency_amount",
            "low",
            ["Price label was absent; verify the extracted amount manually."],
        )
    if len(candidates) > 1:
        return ExtractionResult(
            None,
            None,
            "price.ambiguous_currency_amounts",
            "none",
            ["Several unlabeled currency amounts were found; no price was selected."],
        )
    return ExtractionResult(None, None, "price.not_found", "none", ["No reliable price was found."])


def extract_price(text: str) -> float | None:
    result = extract_price_result(text)
    return float(result.value) if result.value is not None else None
 
 
def _to_float_num(raw: str) -> float | None: 
    if not raw: 
        return None 
 
    clean = raw.replace("\xa0", " ").replace("\u202f", " ") 
    clean = clean.replace(" ", "").replace(",", ".") 
 
    try: 
        return float(clean) 
    except ValueError: 
        return None 
 
 
def extract_building_area(text: str) -> float | None: 
    if not text: 
        return None 

    patterns = [ 
        # "нежилое здание площадью 112,2 кв.м" 
        r'(?:нежилое\s+здание|жилое\s+здание|здание|дом\s+жилой|жилой\s+дом|дом)[^.\n;]{0,140}?(?:площадью|общей площадью|площадь)\s*[:\-]?\s*(\d{1,7}(?:[\s.,]\d{3})*(?:[,.]\d+)?)\s*(?:кв\.?\s*м|м²|м2|м\^2)', 

        # "Здание: 112.2 м2" 
        r'(?:здание|дом)\s*[:\-]\s*(\d{1,7}(?:[\s.,]\d{3})*(?:[,.]\d+)?)\s*(?:кв\.?\s*м|м²|м2|м\^2)', 
    ] 

    for pat in patterns: 
        for m in re.finditer(pat, text, re.IGNORECASE): 
            ctx_start = max(0, m.start() - 80) 
            ctx_end = min(len(text), m.end() + 80) 
            context = text[ctx_start:ctx_end].lower() 

            # Если совпадение явно относится к земельному участку — пропускаем 
            if "земельн" in context and "участ" in context: 
                # Но если в контексте есть и "здание", проверяем ближе к совпадению 
                before = text[max(0, m.start() - 40):m.start()].lower() 
                if "здание" not in before and "дом" not in before: 
                    continue 

            val = _to_float_num(m.group(1)) 
            if val and 1 <= val <= 1_000_000: 
                return val 

    return None 
 
 
def extract_room_area(text: str) -> float | None:
    if not text:
        return None

    patterns = [
        r'(?:нежилое\s+помещение|жилое\s+помещение|помещение|помещения|помещ\.)[^.\n;]{0,140}?(?:площадью|общей площадью|площадь)\s*[:\-]?\s*(\d{1,7}(?:[\s.,]\d{3})*(?:[,.]\d+)?)\s*(?:кв\.?\s*м|м²|м2|м\^2)',
        r'(?:помещение|помещения|помещ\.)\s*[:\-]\s*(\d{1,7}(?:[\s.,]\d{3})*(?:[,.]\d+)?)\s*(?:кв\.?\s*м|м²|м2|м\^2)',
    ]

    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = _to_float_num(m.group(1))
            if val and 1 <= val <= 500_000:
                return val

    return None


def _area_fragment(text: str, value: float) -> str | None:
    for match in re.finditer(
        r"(\d{1,7}(?:[\s.,]\d{3})*(?:[,.]\d+)?)\s*(?:кв\.?\s*м|м²|м2|м\^2)",
        text,
        re.IGNORECASE,
    ):
        parsed = _to_float_num(match.group(1))
        if parsed == value:
            return text[max(0, match.start() - 60):min(len(text), match.end() + 60)].strip()
    return None


def extract_area_result(text: str) -> ExtractionResult:
    if not text:
        return ExtractionResult(None, None, "area.empty", "none", ["Source text is empty."])

    building = extract_building_area(text)
    if building:
        return ExtractionResult(
            building,
            _area_fragment(text, building),
            "area.building_labeled",
            "high",
        )
    room = extract_room_area(text)
    if room:
        return ExtractionResult(room, _area_fragment(text, room), "area.room_labeled", "high")

    values: list[tuple[float, str]] = []
    for match in re.finditer(
        r"(\d{1,7}(?:[\s.,]\d{3})*(?:[,.]\d+)?)\s*(?:кв\.?\s*м|м²|м2|м\^2)",
        text,
        re.IGNORECASE,
    ):
        value = _to_float_num(match.group(1))
        if value and 1 <= value <= 1_000_000:
            values.append((value, match.group(0)))

    if len(values) == 1:
        value, fragment = values[0]
        return ExtractionResult(
            value,
            fragment,
            "area.single_unlabeled",
            "low",
            ["Area type was not identified; verify whether this is building, room, or land area."],
        )
    if len(values) > 1:
        return ExtractionResult(
            None,
            None,
            "area.ambiguous_multiple",
            "none",
            ["Several unlabeled areas were found; no area was selected."],
        )
    return ExtractionResult(None, None, "area.not_found", "none", ["No area was found."])


def extract_area(text: str) -> float | None:
    result = extract_area_result(text)
    return float(result.value) if result.value is not None else None
 
 
def extract_cadastral_numbers(text: str) -> list[str]: 
    if not text: 
        return [] 
 
    nums = re.findall(r'\d{2}:\d{2}:\d{6,7}:\d{1,10}', text) 
    return list(dict.fromkeys(nums)) 
 
 
def extract_cadastral(text: str) -> str | None: 
    nums = extract_cadastral_numbers(text) 
    return nums[0] if nums else None 
 
 
def extract_address(text: str) -> str | None: 
    if not text: 
        return None 
 
    clean = re.sub(r'\s+', ' ', text).strip() 
 
    stop = ( 
        r'(?=Кадастровый номер|Количество этажей|Общая площадь|' 
        r'Категория объекта|Форма собственности|Вид торгов|' 
        r'Вид ограничений|Общие сведения|Назначение|$)' 
    ) 
 
    patterns = [ 
        rf'по адресу:\s*(.+?){stop}', 
        rf'расположен[а-я\s]*по адресу:\s*(.+?){stop}', 
        rf'местоположение:\s*(.+?){stop}', 
        rf'адрес местонахождения:\s*(.+?){stop}', 
        rf'Местонахождение имущества:\s*(.+?){stop}', 
        rf'имущества:\s*((?:обл|область|г\.|город|р-н|район|ул|улица|д\.|дом).+?){stop}', 
    ] 
 
    for pat in patterns: 
        m = re.search(pat, clean, re.IGNORECASE) 
        if not m: 
            continue 
 
        addr = m.group(1).strip(" ,.;:-") 
        addr = re.sub(r'\s+', ' ', addr) 
 
        if len(addr) >= 15: 
            return addr[:350] 
 
    return None 
 
 
def extract_land_area(text: str) -> float | None: 
    if not text: 
        return None 

    patterns = [ 
        # "земельный участок площадью 370 кв.м" 
        r'земельн(?:ый|ого|ом)?\s+участ[а-я]*[^.\n;]{0,160}?(?:площадью|общей площадью|площадь)\s*[:\-]?\s*(\d{1,7}(?:[\s.,]\d{3})*(?:[,.]\d+)?)\s*(сотк[а-я]*|сот\.?|га|кв\.?\s*м|м²|м2|м\^2)?', 

        # "Земельный участок: 19.18 сотки" 
        r'земельн(?:ый|ого|ом)?\s+участ[а-я]*\s*[:\-]\s*(\d{1,7}(?:[\s.,]\d{3})*(?:[,.]\d+)?)\s*(сотк[а-я]*|сот\.?|га|кв\.?\s*м|м²|м2|м\^2)?', 

        # "площадь участка 610 кв.м" 
        r'(?:площадь участка|площадью участка)\s*[:\-]?\s*(\d{1,7}(?:[\s.,]\d{3})*(?:[,.]\d+)?)\s*(сотк[а-я]*|сот\.?|га|кв\.?\s*м|м²|м2|м\^2)?', 
    ] 

    for pat in patterns: 
        m = re.search(pat, text, re.IGNORECASE) 
        if not m: 
            continue 

        val = _to_float_num(m.group(1)) 
        unit = (m.group(2) or "").lower() 

        if not val: 
            continue 

        # Всегда возвращаем м² 
        if "га" in unit: 
            return val * 10_000 

        if "сот" in unit: 
            return val * 100 

        return val 

    return None 
 
 
def extract_floors(text: str) -> int | None: 
    m = re.search(r'(\d+)\s*(?:этаж|эт\.)', text, re.IGNORECASE) 
    return int(m.group(1)) if m else None 
 
 
def extract_legal_status(text: str) -> str | None:
    lower = text.lower()
    if any(x in lower for x in ["банкрот", "конкурсн", "торги по банкротству"]):
        return "банкротные торги"
    if "госимущество" in lower or "муниципал" in lower:
        return "продажа гос. имущества"
    return None


def extract_year_built(text: str) -> int | None:
    """Извлекает год постройки здания"""
    if not text:
        return None

    patterns = [
        r'(?:год постройки|построен[о]?|год строительства)[:\s]*(\d{4})',
        r'(\d{4})\s*г(?:\.|ода)?\s*постройки',
        r'(?:здание|дом)\s+(\d{4})\s*г(?:\.|ода)?',
    ]

    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            year = int(m.group(1))
            if 1800 <= year <= datetime.now().year + 1:
                return year

    return None


def extract_commissioning_year(text: str) -> int | None:
    """Извлекает год ввода в эксплуатацию"""
    if not text:
        return None

    patterns = [
        r'(?:ввод в эксплуатацию|введен[о]? в эксплуатацию)\s+в\s+(\d{4})',
        r'(?:ввод в эксплуатацию|введен[о]? в эксплуатацию)[:\s]+(\d{4})',
        r'(?:год ввода)[:\s]*(\d{4})',
        r'эксплуатаци[ия]\s+с\s+(\d{4})',
    ]

    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            year = int(m.group(1))
            if 1800 <= year <= datetime.now().year + 1:
                return year

    return None


def extract_cultural_heritage(text: str) -> bool:
    """Проверяет, является ли объект объектом культурного наследия (ОКН)"""
    if not text:
        return False

    lower = text.lower()
    keywords = [
        "объект культурного наследия",
        "окн",
        "памятник архитектуры",
        "культурное наследие",
        "охраняемый объект",
        "памятник истории",
        "выявленный объект культурного наследия"
    ]

    return any(kw in lower for kw in keywords) 
