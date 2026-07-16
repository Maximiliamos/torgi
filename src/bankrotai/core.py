from __future__ import annotations

import logging
import os
from urllib.parse import urlparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from datetime import datetime, timezone

# --- Logger ---

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        if hasattr(sys.stdout, "reconfigure"):
            try:
                sys.stdout.reconfigure(errors="replace")
            except Exception:
                pass
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger

# --- Settings ---

DEFAULT_REGION = os.getenv("DEFAULT_REGION_SLUG", "yaroslavl")

REGION_QUERY_ALIASES = {
    "yaroslavl": ("yaroslavl", "76", "84"),
    "76": ("76", "yaroslavl", "84"),
    "84": ("84", "76", "yaroslavl"),
}

REGION_SYNC_ALIASES = {
    "76": "yaroslavl",
    "84": "yaroslavl",
}


def get_region_query_values(region: str | None) -> tuple[str, ...]:
    key = (region or DEFAULT_REGION).strip() or DEFAULT_REGION
    return REGION_QUERY_ALIASES.get(key, (key,))


def get_region_sync_slug(region: str | None) -> str:
    key = (region or DEFAULT_REGION).strip() or DEFAULT_REGION
    return REGION_SYNC_ALIASES.get(key, key)

@dataclass
class RegionalConfig:
    slug: str
    name: str
    search_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    min_discount_threshold: float = 30.0

@dataclass
class AppSettings:
    app_env: str = "dev"
    database_url: str = "sqlite:///bankrotai.db"
    redis_url: str = "redis://localhost:6379/0"
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_model_search: str = "gpt-4o"
    openai_model_risk: str = "gpt-4o-mini"
    tbankrot_api_key: str | None = None

    # AI Provider
    ai_provider: str = "omniroute"  # "omniroute", "openai", "deepseek", "grok", "groq", "opencode", "nvidia", "gemini", "github"
    ai_allow_provider_fallback: bool = False
    deepseek_api_key: str | None = None
    grok_api_key: str | None = None
    groq_api_key: str | None = None
    opencode_api_key: str | None = None
    opencode_api_base: str = "https://api.opencode.ai/v1"
    nvidia_api_key: str | None = None
    kiro_api_key: str | None = None
    gemini_api_key: str | None = None
    github_api_key: str | None = None

    # OmniRoute proxy
    omniroute_api_key: str | None = "sk_omniroute"
    omniroute_api_base: str = "http://localhost:20128"
    omniroute_model: str = "kr/claude-sonnet-4"

    omniroute_protocol: str = "openai"  # "openai" for Kimi/Moonshot, "anthropic" for Claude-compatible routes

    # Models for each provider
    deepseek_model: str = "deepseek-chat"
    grok_model: str = "grok-2"
    groq_model: str = "llama-3.3-70b-versatile"
    opencode_model: str = "gpt-5-nano"
    nvidia_model: str = "nvidia/llama-3.3-nemotron-super-49b-v1"
    gemini_model: str = "gemini-2.5-flash"
    github_model: str = "openai/gpt-4.1-mini"
    kiro_model: str = "kr/claude-sonnet-4"
    kiro_model_search: str = "kr/claude-sonnet-4"
    kiro_model_risk: str = "kr/claude-sonnet-4"

    # Regional configs
    regions: dict[str, RegionalConfig] = field(default_factory=dict)

    # GUI settings
    gui_theme: str = "dark"
    gui_refresh_interval: int = 300 # seconds
    cors_origins: list[str] = field(default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"])
    public_api_key: str | None = None
    api_rate_limit_per_minute: int = 120
    allow_local_task_fallback: bool = False
    sync_retry_max_attempts: int = 4
    sync_retry_backoff_seconds: int = 5
    celery_soft_time_limit: int = 1500
    celery_hard_time_limit: int = 1800
    external_connect_timeout: float = 5.0
    external_read_timeout: float = 30.0
    nspd_ca_bundle: str | None = None
    nspd_allow_insecure_debug: bool = False

    @property
    def is_production(self) -> bool:
        return self.app_env in {"production", "prod"}

    def production_configuration_errors(self) -> list[str]:
        if not self.is_production:
            return []

        errors: list[str] = []
        if not self.public_api_key or len(self.public_api_key) < 24:
            errors.append("BANKROTAI_API_KEY must contain at least 24 characters")

        database = urlparse(self.database_url)
        if database.scheme.startswith("postgres"):
            if not database.password:
                errors.append("DATABASE_URL must contain a PostgreSQL password")
            if (database.username or "").lower() == "postgres" and database.password == "postgres":
                errors.append("DATABASE_URL must not use postgres/postgres")

        redis = urlparse(self.redis_url)
        if redis.scheme.startswith("redis") and not redis.password:
            errors.append("REDIS_URL must contain a Redis password in production")
        return errors

def load_settings() -> AppSettings:
    load_dotenv()

    # Basic settings
    settings = AppSettings(
        app_env=os.getenv("APP_ENV", "dev").lower(),
        database_url=os.getenv("DATABASE_URL", "sqlite:///bankrotai.db"),
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_base_url=os.getenv("OPENAI_BASE_URL"),
        openai_model_search=os.getenv("OPENAI_MODEL_SEARCH", "gpt-4o"),
        openai_model_risk=os.getenv("OPENAI_MODEL_RISK", "gpt-4o-mini"),
        tbankrot_api_key=os.getenv("TBANKROT_API_KEY"),

        # AI Provider settings
        ai_provider=os.getenv("AI_PROVIDER", "omniroute"),
        ai_allow_provider_fallback=os.getenv("AI_ALLOW_PROVIDER_FALLBACK", "false").lower() in {"1", "true", "yes"},
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY"),
        grok_api_key=os.getenv("GROK_API_KEY"),
        groq_api_key=os.getenv("GROQ_API_KEY"),
        opencode_api_key=os.getenv("OPENCODE_API_KEY"),
        opencode_api_base=os.getenv("OPENCODE_API_BASE", "https://api.opencode.ai/v1"),
        nvidia_api_key=os.getenv("NVIDIA_API_KEY"),
        kiro_api_key=os.getenv("KIRO_API_KEY"),
        gemini_api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"),
        github_api_key=os.getenv("GITHUB_MODELS_API_KEY") or os.getenv("GITHUB_TOKEN"),
        omniroute_api_key=os.getenv("OMNIROUTE_API_KEY", "sk_omniroute"),
        omniroute_api_base=os.getenv("OMNIROUTE_API_BASE", "http://localhost:20128"),
        omniroute_model=os.getenv("OMNIROUTE_MODEL", "kr/claude-sonnet-4"),
        omniroute_protocol=os.getenv("OMNIROUTE_PROTOCOL", "openai"),
        deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        grok_model=os.getenv("GROK_MODEL", "grok-2"),
        groq_model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        opencode_model=os.getenv("OPENCODE_MODEL", "gpt-5-nano"),
        nvidia_model=os.getenv("NVIDIA_MODEL", "nvidia/llama-3.3-nemotron-super-49b-v1"),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        github_model=os.getenv("GITHUB_MODEL", "openai/gpt-4.1-mini"),
        kiro_model=os.getenv("KIRO_MODEL", "kr/claude-sonnet-4"),
        public_api_key=os.getenv("BANKROTAI_API_KEY") or os.getenv("WEB_API_KEY"),
        api_rate_limit_per_minute=int(os.getenv("API_RATE_LIMIT_PER_MINUTE", "120")),
        allow_local_task_fallback=os.getenv("ALLOW_LOCAL_TASK_FALLBACK", "false").lower() in {"1", "true", "yes"},
        sync_retry_max_attempts=int(os.getenv("SYNC_RETRY_MAX_ATTEMPTS", "4")),
        sync_retry_backoff_seconds=int(os.getenv("SYNC_RETRY_BACKOFF_SECONDS", "5")),
        celery_soft_time_limit=int(os.getenv("CELERY_SOFT_TIME_LIMIT", "1500")),
        celery_hard_time_limit=int(os.getenv("CELERY_HARD_TIME_LIMIT", "1800")),
        external_connect_timeout=float(os.getenv("EXTERNAL_CONNECT_TIMEOUT", "5")),
        external_read_timeout=float(os.getenv("EXTERNAL_READ_TIMEOUT", "30")),
        nspd_ca_bundle=os.getenv("NSPD_CA_BUNDLE") or None,
        nspd_allow_insecure_debug=os.getenv("NSPD_ALLOW_INSECURE_DEBUG", "false").lower() in {"1", "true", "yes"},
    )
    cors_raw = os.getenv("CORS_ORIGINS", "")
    if cors_raw:
        settings.cors_origins = [origin.strip() for origin in cors_raw.split(",") if origin.strip()]

    # Predefined regions (e.g., Yaroslavl)
    yaroslavl = RegionalConfig(
        slug="yaroslavl",
        name="Ярославская область",
        search_keywords=["ярославль", "рыбинск", "переславль"],
        min_discount_threshold=25.0,
    )
    settings.regions[yaroslavl.slug] = yaroslavl

    return settings

_settings_cache: AppSettings | None = None

def get_settings() -> AppSettings:
    global _settings_cache
    if _settings_cache is None:
        _settings_cache = load_settings()
    return _settings_cache

def get_app_setting(key: str, default: str | None = None) -> str | None:
    if key.endswith("_api_key") or key in {"telegram_bot_token", "public_api_key"}:
        return default
    from bankrotai.db import session_scope, AppSetting, select
    try:
        with session_scope() as s:
            setting = s.scalar(select(AppSetting).where(AppSetting.key == key))
            if setting:
                return setting.value
            return default
    except Exception:
        return default

def set_app_setting(key: str, value: str):
    if key.endswith("_api_key") or key in {"telegram_bot_token", "public_api_key"}:
        raise ValueError(f"Secret setting {key!r} must be supplied through the environment or a secret manager")
    from bankrotai.db import session_scope, AppSetting, select
    with session_scope() as s:
        setting = s.scalar(select(AppSetting).where(AppSetting.key == key))
        if setting:
            setting.value = value
        else:
            s.add(AppSetting(key=key, value=value))
