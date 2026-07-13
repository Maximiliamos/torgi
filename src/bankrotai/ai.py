from __future__ import annotations

import json
import logging
import os
import re
import sys

# Prevent NVIDIA driver probing in Trae sandbox
os.environ["NVCUVID_DISABLE_DEVICE_PROBE"] = "1"
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
os.environ["OTT_DISABLE_NVML"] = "1"

from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from bankrotai.core import get_logger, get_settings
from bankrotai.domain import LotEvaluation, MarketAssessment, NormalizedLot, RiskAssessment
from bankrotai.logic import (
    calculate_discount_percent,
    calculate_rating,
    needs_human_review
)

logger = get_logger("ai")


MARKET_SYSTEM_PROMPT = """
Ты профессиональный оценщик имущества на торгах в РФ.
Оцени ориентировочную справедливую цену объекта по данным, которые передал пользователь.
Если у тебя нет доступа к актуальным аналогам или данных недостаточно, всё равно верни осторожную оценку
по стартовой/текущей цене и явно поставь confidence="low".
Не отказывайся от ответа из-за нехватки рыночных данных; вместо этого опиши ограничения в explanation.
Обязательно верни JSON объект с полями:
- "market_price": число, без научной нотации
- "min_price": число, без научной нотации
- "max_price": число, без научной нотации
- "confidence": "high", "medium" или "low"
- "explanation": текст с обоснованием
- "links": список ссылок на аналоги, если они явно есть в исходных данных

Верни только чистый JSON, без markdown.
"""

RISK_SYSTEM_PROMPT = """
Ты аналитик торгов и ликвидности имущества. Оцени риски покупки по данным пользователя.
Верни JSON объект с полями:
- "risk_score": число от 1 до 10, где 10 - максимальный риск
- "recommendation": текст рекомендации
- "time_to_sell": примерный срок продажи текстом

Верни только чистый JSON, без markdown.
"""

def _strip_json_markdown_fence(raw: str) -> str:
    text = raw.strip()
    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()

def _extract_json_object_text(raw: str) -> str:
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        return raw[start:end + 1].strip()
    return raw.strip()

def _repair_common_json_object_mistakes(raw: str) -> str:
    repaired = _extract_json_object_text(raw)
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    repaired = re.sub(
        r'([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)',
        r'\1"\2"\3',
        repaired,
    )
    return repaired

def _parse_json_object(content: str) -> dict:
    raw = _strip_json_markdown_fence(content or "")
    candidates = [
        raw,
        _extract_json_object_text(raw),
        _repair_common_json_object_mistakes(raw),
    ]
    last_exc: Exception | None = None
    for candidate in candidates:
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
            if isinstance(payload, dict):
                return payload
            raise ValueError(f"expected JSON object, got {type(payload).__name__}")
        except Exception as exc:
            last_exc = exc

    snippet = (content or "")[:500].replace("\n", " ")
    raise RuntimeError(f"AI returned invalid JSON: {last_exc}; response starts with: {snippet!r}") from last_exc

def _require_json_fields(payload: dict, fields: tuple[str, ...], response_type: str) -> None:
    missing = [field for field in fields if field not in payload]
    if missing:
        raise RuntimeError(
            f"AI returned invalid {response_type} JSON; missing fields: {', '.join(missing)}"
        )

def _valuation_sanity_floor(lot: NormalizedLot) -> float | None:
    """Conservative guardrail for rural buildings where LLMs copy auction min-price."""
    area = lot.total_area_gba or lot.area
    current_price = float(lot.current_price or lot.start_price or 0)
    text = f"{lot.title} {lot.description} {lot.address or ''}".lower()
    category = (lot.category or "").lower()
    has_building = category in {"house", "commercial"} or "здание" in text or "помещение" in text
    if not has_building:
        return None

    if area and area >= 1000:
        return current_price * 0.85 if current_price else None

    if not area:
        if current_price and current_price < 50_000 and ("здание" in text or category == "commercial"):
            return 600_000
        return None

    if category == "commercial" or "помещение" in text:
        if area < 80:
            return 65_000
        return max(120_000, min(area * 1_200, 350_000))

    if "культурного наследия" in text or "культурн" in text:
        return 120_000
    if area < 80:
        return 120_000
    if area < 125:
        return 250_000 if "земельн" in text and area >= 114 else 200_000
    if area < 250:
        return 250_000
    return 250_000

def _apply_market_sanity(lot: NormalizedLot, market: MarketAssessment) -> MarketAssessment:
    floor = _valuation_sanity_floor(lot)
    if not floor or market.market_price >= floor:
        return market

    market.market_price = float(floor)
    market.min_price = float(max(market.min_price or 0, floor * 0.7))
    market.max_price = float(max(market.max_price or 0, floor * 1.3))
    market.confidence = "low"
    note = (
        "Применена защитная минимальная оценка для здания/помещения: "
        "AI-ответ был ниже консервативного порога по площади и типу объекта."
    )
    market.explanation = f"{note} {market.explanation}".strip()
    return market

def is_retryable(exception: Exception) -> bool:
    if isinstance(exception, APIStatusError):
        # 429 is Too Many Requests, 500/502/503/504 are server errors
        return exception.status_code in (429, 500, 502, 503, 504)
    return isinstance(exception, (APIConnectionError, APITimeoutError, RateLimitError, TimeoutError, ConnectionError))

def _normalize_omniroute_openai_base_url(base_url: str | None) -> str:
    normalized = (base_url or "http://localhost:20128").rstrip("/")
    if normalized.endswith("/v1"):
        return normalized
    return f"{normalized}/v1"

def _is_local_omniroute_base_url(base_url: str | None) -> bool:
    try:
        host = urlparse(base_url or "http://localhost:20128").hostname or ""
    except Exception:
        return False
    return host.lower() in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

class AIProvider:
    def __init__(self, settings: Any):
        from bankrotai.core import get_app_setting
        self.settings = settings
        # Resolve provider first
        self.provider = get_app_setting("ai_provider", settings.ai_provider) or "deepseek"
        self.omniroute_protocol = "openai"
        self._anthropic = None
        self.client = self._create_client()

    def _create_client(self):
        from bankrotai.core import get_app_setting
        provider = self.provider

        # Priority: DB setting -> .env setting -> default base_url
        if provider == "openai":
            api_key = get_app_setting("openai_api_key", self.settings.openai_api_key)
            base_url = self.settings.openai_base_url
        elif provider == "deepseek":
            api_key = get_app_setting("deepseek_api_key", self.settings.deepseek_api_key)
            base_url = "https://api.deepseek.com/v1"
        elif provider == "grok":
            api_key = get_app_setting("grok_api_key", self.settings.grok_api_key)
            base_url = "https://api.x.ai/v1"
        elif provider == "groq":
            api_key = get_app_setting("groq_api_key", self.settings.groq_api_key)
            base_url = "https://api.groq.com/openai/v1"
        elif provider == "github":
            api_key = get_app_setting("github_api_key", self.settings.github_api_key)
            base_url = "https://models.github.ai/inference"
            if not api_key:
                logger.warning("GitHub Models API key is missing")
            return OpenAI(
                api_key=api_key,
                base_url=base_url,
                default_headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        elif provider == "opencode":
            api_key = get_app_setting("opencode_api_key", self.settings.opencode_api_key)
            base_url = get_app_setting("opencode_api_base", self.settings.opencode_api_base)
        elif provider == "nvidia":
            api_key = get_app_setting("nvidia_api_key", self.settings.nvidia_api_key)
            base_url = "https://integrate.api.nvidia.com/v1"
        elif provider == "gemini":
            api_key = get_app_setting("gemini_api_key", self.settings.gemini_api_key)
            base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
        elif provider == "kiro":
            api_key = get_app_setting("kiro_api_key", self.settings.kiro_api_key)
            base_url = "https://api.kiro.ai/v1"
        elif provider == "omniroute":
            api_key = get_app_setting("omniroute_api_key", self.settings.omniroute_api_key)
            base_url = get_app_setting("omniroute_api_base", self.settings.omniroute_api_base)
            protocol = get_app_setting(
                "omniroute_protocol",
                getattr(self.settings, "omniroute_protocol", "openai"),
            )
            self.omniroute_protocol = (protocol or "openai").strip().lower()
            if not api_key:
                logger.warning("OmniRoute API key is missing")
            is_local = _is_local_omniroute_base_url(base_url)
            auth_key = "sk_omniroute" if is_local else (api_key or "sk_omniroute")
            if self.omniroute_protocol in {"anthropic", "claude", "messages"}:
                from anthropic import Anthropic
                self._anthropic = Anthropic(
                    api_key=auth_key,
                    base_url=(base_url or "http://localhost:20128").rstrip("/"),
                )
                return None # OpenAI client not needed
            client_kwargs = {
                "api_key": auth_key,
                "base_url": _normalize_omniroute_openai_base_url(base_url),
            }
            if is_local:
                client_kwargs["default_headers"] = {"Authorization": ""}
            elif api_key:
                client_kwargs["default_headers"] = {"x-api-key": api_key}
            return OpenAI(**client_kwargs)
        else:
            logger.warning(f"Unsupported AI provider: {provider}, falling back to deepseek")
            self.provider = "deepseek"
            return self._create_client()

        if not api_key:
            # Try to fallback to any available key
            logger.warning(f"API key for {provider} is missing. Trying to find any available key...")
            for p in ["gemini", "groq", "grok", "github", "omniroute", "kiro", "deepseek", "openai", "nvidia"]:
                key = get_app_setting(f"{p}_api_key", getattr(self.settings, f"{p}_api_key", None))
                if key:
                    logger.info(f"Falling back to {p} provider")
                    self.provider = p
                    return self._create_client()
            raise RuntimeError(f"No AI API keys found. Please configure at least one provider (Gemini, GroqCloud, Grok/xAI, GitHub Models, Kiro, OpenAI, DeepSeek, or NVIDIA).")

        return OpenAI(api_key=api_key, base_url=base_url)

    def get_model(self, model_type: str = "search") -> str:
        from bankrotai.core import get_app_setting
        provider = self.provider
        if provider == "openai":
            if model_type == "search":
                return get_app_setting("openai_model_search", self.settings.openai_model_search) or "gpt-4o"
            return get_app_setting("openai_model_risk", self.settings.openai_model_risk) or "gpt-4o-mini"

        if provider == "kiro":
            if model_type == "search":
                return get_app_setting("kiro_model_search", self.settings.kiro_model_search) or "kr/claude-sonnet-4"
            return get_app_setting("kiro_model_risk", self.settings.kiro_model_risk) or "kr/claude-sonnet-4"

        if provider == "omniroute":
            return get_app_setting("omniroute_model", self.settings.omniroute_model) or "kr/claude-sonnet-4"

        # For other providers, use the generic 'model' setting or fallback
        setting_key = f"{provider}_model"
        default_model = (
            "deepseek-chat" if provider == "deepseek"
            else "gemini-2.5-flash" if provider == "gemini"
            else "grok-4" if provider == "grok"
            else "llama-3.3-70b-versatile" if provider == "groq"
            else "openai/gpt-4.1-mini" if provider == "github"
            else "meta/llama-3.3-70b-instruct"
        )
        return get_app_setting(setting_key, getattr(self.settings, setting_key, default_model)) or default_model

class OpenAIAppraiser:
    def __init__(self) -> None:
        settings = get_settings()
        self.settings = settings
        self.provider = AIProvider(settings)
        self.client = self.provider.client

    def _get_cache_key(self, lot: NormalizedLot) -> str:
        # Generate unique key based on lot data and AI models used
        import hashlib
        data = {
            "external_id": lot.external_id,
            "price": str(lot.current_price or lot.start_price or ""),
            "provider": self.provider.provider,
            "search_model": self.provider.get_model("search"),
            "risk_model": self.provider.get_model("risk"),
            "title": lot.title,
            "description": lot.description,
            "address": lot.address,
            "cadastral_number": lot.cadastral_number,
            "area": lot.area,
            "total_area_gba": lot.total_area_gba,
            "land_area": lot.land_area,
            "category": lot.category,
        }
        payload = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @retry(
        stop=stop_after_attempt(8),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception(is_retryable),
        reraise=True
    )
    def _fetch_completion(self, messages: list[dict[str, str]], model_type: str = "search") -> str:
        """Выполняет запрос к AI и универсально извлекает текст ответа."""
        try:
            model = self.provider.get_model(model_type)

            # For debugging, log the request
            logger.info(f"AI Request ({self.provider.provider}): model={model}")

            if (
                self.provider.provider == "omniroute"
                and self.provider.omniroute_protocol in {"anthropic", "claude", "messages"}
                and self.provider._anthropic
            ):
                # Используем Anthropic Messages API для OmniRoute.
                from anthropic import NOT_GIVEN
                system_prompt = ""
                user_messages = []
                for m in messages:
                    if m["role"] == "system":
                        system_prompt += m["content"] + "\n"
                    else:
                        user_messages.append(m)

                # Anthropic ожидает массив сообщений с чередующимися ролями.
                user_content = ""
                for m in user_messages:
                    user_content += f"{m['role'].upper()}: {m['content']}\n\n"

                logger.info(f"Anthropic request: model={model}, base={self.provider._anthropic.base_url}")

                response = self.provider._anthropic.messages.create(
                    model=model,
                    max_tokens=2048,
                    temperature=0.2 if model_type == "search" else 0.4,
                    system=system_prompt.strip() or NOT_GIVEN,
                    messages=[{"role": "user", "content": user_content.strip()}]
                )
                content = response.content[0].text
            else:
                # OpenAI/DeepSeek support json_object, NVIDIA might be better without it
                response_format = {"type": "json_object"} if self.provider.provider in ["openai", "deepseek", "kiro", "omniroute"] else None

                # Низкая температура для оценки рынка (search), выше для рисков.
                temperature = 0.2 if model_type == "search" else 0.4

                if not self.client:
                    raise RuntimeError(f"Client for provider {self.provider.provider} is not initialized")

                response = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    response_format=response_format,
                    temperature=temperature
                )

                content = ""
                # Обрабатываем разные форматы ответа.
                if isinstance(response, str):
                    content = response
                elif hasattr(response, 'choices') and response.choices:
                    content = response.choices[0].message.content
                elif hasattr(response, 'text'):
                    content = response.text
                elif isinstance(response, dict):
                    content = response.get("choices", [{}])[0].get("message", {}).get("content", "")

            logger.info(f"AI Response ({self.provider.provider}): {content[:200]}")

            if not content:
                raise RuntimeError("Empty response from AI")

            return content
        except Exception as e:
            logger.error(f"Ошибка при вызове AI ({self.provider.provider}): {e}")
            raise

    def evaluate(self, lot: NormalizedLot, session: Any | None = None) -> LotEvaluation:
        cache_key = self._get_cache_key(lot)

        if session:
            from bankrotai.db import ValuationRun
            from sqlalchemy import select
            cached = session.scalar(
                select(ValuationRun)
                .where(ValuationRun.content_hash == cache_key)
                .order_by(ValuationRun.appraised_at.desc())
            )
            if cached and cached.valuation_snapshot:
                logger.info("Cache hit for lot %s", lot.external_id)
                data = cached.valuation_snapshot
                return LotEvaluation(
                    market=MarketAssessment(**data.get("market", {})),
                    risk=RiskAssessment(**data.get("risk", {}))
                )

        market = self.assess_market(lot)
        risk = self.assess_risk(lot, market)
        evaluation = LotEvaluation(market=market, risk=risk)

        if session:
            self._save_evaluation_to_db(session, lot, evaluation, cache_key)

        return evaluation

    def _save_evaluation_to_db(self, session: Any, lot: NormalizedLot, evaluation: LotEvaluation, cache_key: str) -> None:
        from bankrotai.db import ValuationRun, ProcessedLot
        from sqlalchemy import select

        db_lot = session.scalar(select(ProcessedLot).where(ProcessedLot.external_id == lot.external_id))
        if db_lot:
            run = ValuationRun(
                lot_id=db_lot.id,
                content_hash=cache_key,
                valuation_method="openai",
                valuation_snapshot={
                    "market": {k: getattr(evaluation.market, k) for k in evaluation.market.__slots__},
                    "risk": {k: getattr(evaluation.risk, k) for k in evaluation.risk.__slots__}
                }
            )
            session.add(run)
            session.flush()
            logger.info("Saved AI evaluation to DB for lot %s", lot.external_id)

    def _build_user_prompt(self, lot: NormalizedLot) -> str:
        parts = [
            f"Лот: {lot.title}",
            f"Описание: {lot.description[:500]}",
            f"Адрес: {lot.address or 'не указан'}",
            f"Кадастровый номер: {lot.cadastral_number or '-'}",
            f"Общая площадь: {lot.total_area_gba or lot.area or '-'} м2",
            f"Площадь участка: {lot.land_area or '-'}",
            f"Этажность: {lot.floors or '-'}",
            f"Юридический статус: {lot.legal_status or '-'}",
            f"Текущая цена торгов: {lot.current_price or lot.start_price}",
        ]
        return "\n".join(parts)

    def assess_market(self, lot: NormalizedLot) -> MarketAssessment:
        messages = [
            {"role": "system", "content": MARKET_SYSTEM_PROMPT.strip()},
            {"role": "user", "content": self._build_user_prompt(lot).strip()}
        ]
        content = self._fetch_completion(messages, "search")
        payload = _parse_json_object(content)
        _require_json_fields(payload, ("market_price", "min_price", "max_price"), "market")

        market = MarketAssessment(
            market_price=float(payload.get("market_price", 0)),
            min_price=float(payload.get("min_price", 0)),
            max_price=float(payload.get("max_price", 0)),
            confidence=payload.get("confidence", "low"),
            explanation=payload.get("explanation", ""),
            links=payload.get("links", [])
        )
        return _apply_market_sanity(lot, market)

    def assess_risk(self, lot: NormalizedLot, market: MarketAssessment) -> RiskAssessment:
        details = self._build_user_prompt(lot)
        user_prompt = (
            f"{details}\n\n"
            f"Рыночная цена: {market.market_price}\n"
            f"Уверенность оценки: {market.confidence}\n"
            f"Аргументация цены: {market.explanation}"
        )
        messages = [
            {"role": "system", "content": RISK_SYSTEM_PROMPT.strip()},
            {"role": "user", "content": user_prompt.strip()}
        ]
        content = self._fetch_completion(messages, "risk")
        payload = _parse_json_object(content)
        _require_json_fields(payload, ("risk_score", "recommendation", "time_to_sell"), "risk")

        return RiskAssessment(
            risk_score=int(payload.get("risk_score", 5)),
            recommendation=payload.get("recommendation", ""),
            time_to_sell=payload.get("time_to_sell", "unknown")
        )

def apply_evaluation_to_lot(processed_lot: Any, evaluation: LotEvaluation) -> None:
    processed_lot.market_price = Decimal(f"{evaluation.market.market_price:.2f}")
    processed_lot.market_price_min = Decimal(f"{evaluation.market.min_price:.2f}")
    processed_lot.market_price_max = Decimal(f"{evaluation.market.max_price:.2f}")
    processed_lot.discount_percent = calculate_discount_percent(evaluation.market.market_price, float(processed_lot.current_price or processed_lot.start_price or 0))
    processed_lot.risk_score = evaluation.risk.risk_score
    processed_lot.ai_recommendation = f"{evaluation.risk.recommendation}\n\nОжидаемый срок продажи: {evaluation.risk.time_to_sell}"
    processed_lot.rating = calculate_rating(
        discount_percent=processed_lot.discount_percent,
        risk_score=processed_lot.risk_score,
        status=processed_lot.auction_status,
        confidence=evaluation.market.confidence,
        category=processed_lot.category,
        legal_status=processed_lot.legal_status,
        address=processed_lot.address,
        area=processed_lot.area,
    )
    processed_lot.links_to_analogs = evaluation.market.links
    processed_lot.needs_human_review = needs_human_review(evaluation.market.confidence)
