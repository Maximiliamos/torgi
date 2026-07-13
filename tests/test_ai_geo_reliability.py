from __future__ import annotations

import pytest
from types import SimpleNamespace
from pydantic import ValidationError
from requests.exceptions import SSLError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from bankrotai import core
from bankrotai.ai import MarketResultModel, OpenAIAppraiser, RiskResultModel, validate_ai_evaluation
from bankrotai.db import Base, ProcessedLot, ValuationRun
from bankrotai.domain import NormalizedLot
from bankrotai.geo import CadastralGeocoder, NSPDTLSVerificationError, NominatimGeocoder, nspd_tls_verify


VALID_MARKET = {
    "market_price": 1_000_000,
    "min_price": 900_000,
    "max_price": 1_100_000,
    "confidence": "medium",
    "explanation": "Comparable sales support this range.",
    "links": ["https://example.com/analog"],
}


@pytest.mark.parametrize(
    "changes",
    [
        {"market_price": -1},
        {"min_price": 1_200_000},
        {"confidence": "certain"},
        {"links": ["not-a-url"]},
    ],
)
def test_invalid_market_ai_results_are_rejected(changes: dict) -> None:
    with pytest.raises(ValidationError):
        MarketResultModel.model_validate({**VALID_MARKET, **changes})


@pytest.mark.parametrize("risk", [0, 11])
def test_risk_outside_business_range_is_rejected(risk: int) -> None:
    with pytest.raises(ValidationError):
        RiskResultModel.model_validate({
            "risk_score": risk,
            "recommendation": "Review documents",
            "time_to_sell": "6 months",
        })


def test_valid_ai_result_is_converted_to_domain_model() -> None:
    result = validate_ai_evaluation(
        VALID_MARKET,
        {"risk_score": 4, "recommendation": "Proceed with review", "time_to_sell": "6 months"},
    )
    assert result.market.market_price == 1_000_000
    assert result.risk.risk_score == 4


def test_real_provider_and_models_are_saved_with_successful_evaluation() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    lot = NormalizedLot(
        external_id="ai-lot", source="test", source_system="test", title="Lot", description="Description",
        category="land", region_slug="76", region_name=None, address=None, cadastral_number=None, vin=None,
        area=None, start_price=100.0, current_price=100.0, auction_status="active", lot_url=None,
        source_url=None, detail_level="detail", raw_data={},
    )
    evaluation = validate_ai_evaluation(
        VALID_MARKET,
        {"risk_score": 4, "recommendation": "Review", "time_to_sell": "6 months"},
    )
    appraiser = object.__new__(OpenAIAppraiser)
    appraiser.provider = SimpleNamespace(
        provider="nvidia",
        get_model=lambda kind: "market-model" if kind == "search" else "risk-model",
    )
    with Session(engine) as session:
        session.add(ProcessedLot(
            external_id="ai-lot", source="test", source_system="test", title="Lot", description="Description",
            category="land", region_slug="76", auction_status="active",
        ))
        session.flush()
        appraiser._save_evaluation_to_db(session, lot, evaluation, "hash", duration_ms=123)
        run = session.scalar(select(ValuationRun))
        assert run is not None
        assert run.provider == "nvidia"
        assert run.model == "market-model|risk-model"
        assert run.duration_ms == 123


def test_production_nspd_tls_verification_cannot_be_disabled(monkeypatch) -> None:
    settings = core.AppSettings(app_env="production", nspd_allow_insecure_debug=True)
    monkeypatch.setattr("bankrotai.geo.get_settings", lambda: settings)
    assert nspd_tls_verify() is True


def test_nspd_tls_error_is_classified(monkeypatch) -> None:
    monkeypatch.setattr("bankrotai.geo.requests.get", lambda *args, **kwargs: (_ for _ in ()).throw(SSLError("bad cert")))
    with pytest.raises(NSPDTLSVerificationError):
        CadastralGeocoder()._search_nspd_geoportal("76:23:010101:10")


def test_same_normalized_address_uses_geocoder_cache(monkeypatch) -> None:
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [{"lat": "57.6", "lon": "39.8", "importance": 0.8}]

    def get(*args, **kwargs):
        calls.append((args, kwargs))
        return Response()

    monkeypatch.setattr("bankrotai.geo.requests.get", get)
    geocoder = NominatimGeocoder()
    first = geocoder.geocode("Ярославль, улица Свободы, 1")
    second = geocoder.geocode("  ЯРОСЛАВЛЬ,   УЛИЦА СВОБОДЫ, 1 ")
    assert first == second
    assert len(calls) == 1
