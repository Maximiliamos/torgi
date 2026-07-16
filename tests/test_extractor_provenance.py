from __future__ import annotations

from bankrotai.extractors import extract_area, extract_area_result, extract_price, extract_price_result


def test_price_prefers_labeled_price_over_deposit_and_area() -> None:
    text = (
        "Площадь 12 500 м2. Начальная цена: 2 400 000 000 руб. "
        "Задаток 240 000 000 руб."
    )
    result = extract_price_result(text)
    assert result.value == 2_400_000_000
    assert result.rule_id == "price.start_price"
    assert result.confidence == "high"
    assert result.source_fragment
    assert extract_price(text) == result.value


def test_price_does_not_guess_between_unlabeled_amounts() -> None:
    result = extract_price_result("Суммы: 1 000 000 руб. и 2 000 000 руб.")
    assert result.value is None
    assert result.rule_id == "price.ambiguous_currency_amounts"
    assert result.warnings


def test_area_does_not_choose_maximum_when_types_are_ambiguous() -> None:
    text = "Земля 10 000 м2, строение 500 м2."
    result = extract_area_result(text)
    assert result.value is None
    assert result.rule_id == "area.ambiguous_multiple"
    assert extract_area(text) is None


def test_labeled_building_area_has_provenance() -> None:
    text = "Нежилое здание общей площадью 10215.5 м2, участок 25000 м2."
    result = extract_area_result(text)
    assert result.value == 10215.5
    assert result.rule_id == "area.building_labeled"
    assert result.confidence == "high"
    assert result.source_fragment
