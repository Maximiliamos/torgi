from pathlib import Path 
import pytest
from bankrotai.scrapers import ManualHtmlParser 
from bankrotai.extractors import extract_area, extract_cadastral_numbers 

def test_parse_tbankrot_saved_html(): 
    # Пытаемся найти файл "Новая выгрузка.html" в корне проекта или текущей папке
    file_path = Path("Новая выгрузка.html")
    if not file_path.exists():
        # Если файла нет, создаем минимальный mock-HTML для теста селекторов
        html = """
        <div class="lot_container">
            <div class="lot" data-id="12345">
                <p class="lot_title"><a href="/lot/12345">Продажа нежилого здания 1000 м2</a></p>
                <a class="lot_num">Лот №1</a>
                <div class="lot_description"><div class="text">Описание лота по адресу: г. Ярославль, ул. Свободы, 1. Кадастровый номер: 76:23:010101:100</div></div>
                <div class="lot_prices">
                    <div class="current_price"><span>10 000 000 руб.</span></div>
                    <div class="minimal_price"><span class="small">Задаток</span><span class="green-color">1 000 000 руб.</span></div>
                </div>
                <div class="lot_created">01.01.2026</div>
                <div class="status_icon green"></div>
            </div>
        </div>
        """
    else:
        html = file_path.read_text(encoding="utf-8", errors="replace") 
 
    parser = ManualHtmlParser() 
    lots = parser.parse_html(html) 
 
    assert len(lots) > 0 
 
    # Проверяем, что название не равно номеру 
    for lot in lots[:20]: 
        assert not lot.title.strip().isdigit() 
        assert "tbankrot.ru" in lot.url
        assert lot.external_id 
 
    # Проверяем, что есть цены 
    priced = [lot for lot in lots if lot.current_price] 
    assert len(priced) > 0 
 
    # Проверяем, что цена не берётся из задатка/шага 
    for lot in priced[:20]: 
        assert lot.current_price > 0 
        # В моке 10 000 000, задаток 1 000 000. Если возьмет задаток - ошибка.
        if lot.external_id == "12345":
            assert lot.current_price == 10000000.0
        assert "руб" in lot.price_text.lower() or "₽" in lot.price_text
 
    # Проверяем описание 
    for lot in lots[:20]: 
        assert len(lot.description) > 10 

def test_large_area_not_truncated(): 
    text = "Нежилое здание площадью 10215.5 м², кадастровый номер: 76:22:010305:530" 
    assert extract_area(text) == 10215.5 
 
def test_multiple_cadastral_numbers(): 
    text = "участок 76:22:010305:18 и здание 76:22:010305:530" 
    nums = extract_cadastral_numbers(text)
    assert "76:22:010305:18" in nums
    assert "76:22:010305:530" in nums
