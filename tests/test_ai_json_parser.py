from bankrotai.ai import _parse_json_object


def test_parse_json_object_repairs_unquoted_first_key() -> None:
    payload = _parse_json_object(
        """
        {
          market_price": 855840,
          "min_price": 684672,
          "max_price": 1027008,
          "confidence": "low",
          "explanation": "limited market data"
        }
        """.replace('market_price"', "market_price")
    )

    assert payload["market_price"] == 855840
    assert payload["confidence"] == "low"


def test_parse_json_object_extracts_json_from_text() -> None:
    payload = _parse_json_object(
        'Result: { risk_score: 4, "recommendation": "hold", "time_to_sell": "3 months" }'
    )

    assert payload["risk_score"] == 4
    assert payload["time_to_sell"] == "3 months"
