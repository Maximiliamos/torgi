from bankrotai.logic import (
    classify_category,
    calculate_discount_percent,
    calculate_potential_profit,
    calculate_rating,
    needs_human_review
)


def test_discount_percent() -> None:
    assert calculate_discount_percent(10_000_000, 7_500_000) == 25.0


def test_potential_profit() -> None:
    assert calculate_potential_profit(10_000_000, 7_500_000) == 2_500_000


def test_rating_formula() -> None:
    assert calculate_rating(25.0, 4) == 4.75


def test_human_review_flag() -> None:
    assert needs_human_review("низкая") is True
    assert needs_human_review("высокая") is False


def test_category_classifier() -> None:
    assert classify_category("Квартира 65 кв.м", "") == "apartment"
    assert classify_category("Грузовой автомобиль", "") == "car"
    assert classify_category("Право требования долга", "") == "receivable"
    assert classify_category("Земельный участок", "") == "land"
