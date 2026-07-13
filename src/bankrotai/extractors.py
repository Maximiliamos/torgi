import re 
 
def extract_price(text: str) -> float | None: 
    """Максимально надёжное извлечение основной цены лота""" 
    if not text: 
        return None 
    
    clean = re.sub(r'[\xa0\u202f\u2009]', ' ', text) 
    clean = re.sub(r'\s+', ' ', clean).strip() 
    
    # Ищем все числа 
    matches = re.findall(r'(\d{1,3}(?:[\s.,]\d{3})*(?:[.,]\d+)?)', clean) 
    if not matches: 
        return None 
    
    candidates = [] 
    for m in matches: 
        raw = m.replace(" ", "").replace(",", ".") 
        try: 
            val = float(raw) 
            if 1000 < val < 1_000_000_000:   # разумный диапазон для лотов 
                candidates.append(val) 
        except: 
            continue 
    
    if not candidates: 
        return None 
    
    # Берём самое большое число — почти всегда это стартовая/текущая цена 
    return max(candidates) 
 
 
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


def extract_area(text: str) -> float | None: 
    if not text: 
        return None 
 
    area = extract_building_area(text) or extract_room_area(text) 
    if area: 
        return area 
 
    matches = re.findall( 
        r'(\d{1,7}(?:[\s.,]\d{3})*(?:[,.]\d+)?)\s*(?:кв\.?\s*м|м²|м2|м\^2)', 
        text, 
        re.IGNORECASE 
    ) 
 
    values = [] 
    for raw in matches: 
        val = _to_float_num(raw) 
        if val and 1 <= val <= 1_000_000: 
            values.append(val) 
 
    return max(values) if values else None 
 
 
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
            # Проверка на разумный диапазон (1800-2030)
            if 1800 <= year <= 2030:
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
            if 1800 <= year <= 2030:
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
