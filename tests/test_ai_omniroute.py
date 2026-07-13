from types import SimpleNamespace

import bankrotai.ai as ai
import bankrotai.core as core
from bankrotai.ai import AIProvider


class FakeOpenAI:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeAnthropic:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _settings(protocol: str = "openai") -> SimpleNamespace:
    return SimpleNamespace(
        ai_provider="omniroute",
        omniroute_api_key="sk-test",
        omniroute_api_base="http://localhost:20128",
        omniroute_protocol=protocol,
    )


def _fake_app_setting(protocol: str = "openai"):
    values = {
        "ai_provider": "omniroute",
        "omniroute_api_key": "sk-test",
        "omniroute_api_base": "http://localhost:20128",
        "omniroute_protocol": protocol,
    }
    return lambda key, default=None: values.get(key, default)


def test_omniroute_defaults_to_openai_compatible_client(monkeypatch) -> None:
    monkeypatch.setattr(core, "get_app_setting", _fake_app_setting("openai"))
    monkeypatch.setattr(ai, "OpenAI", FakeOpenAI)

    provider = AIProvider(_settings("openai"))

    assert provider.client.kwargs["api_key"] == "sk_omniroute"
    assert provider.client.kwargs["base_url"] == "http://localhost:20128/v1"
    assert provider.client.kwargs["default_headers"] == {"Authorization": ""}
    assert provider._anthropic is None
    assert provider.omniroute_protocol == "openai"


def test_omniroute_can_use_anthropic_protocol(monkeypatch) -> None:
    import anthropic

    monkeypatch.setattr(core, "get_app_setting", _fake_app_setting("anthropic"))
    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)

    provider = AIProvider(_settings("anthropic"))

    assert provider.client is None
    assert provider._anthropic.kwargs["api_key"] == "sk_omniroute"
    assert provider._anthropic.kwargs["base_url"] == "http://localhost:20128"
    assert provider.omniroute_protocol == "anthropic"


def test_gemini_uses_google_openai_compatible_endpoint(monkeypatch) -> None:
    settings = SimpleNamespace(
        ai_provider="gemini",
        gemini_api_key="gemini-test-key",
        gemini_model="gemini-2.5-flash",
    )
    values = {
        "ai_provider": "gemini",
        "gemini_api_key": "gemini-test-key",
        "gemini_model": "gemini-2.5-flash",
    }

    monkeypatch.setattr(core, "get_app_setting", lambda key, default=None: values.get(key, default))
    monkeypatch.setattr(ai, "OpenAI", FakeOpenAI)

    provider = AIProvider(settings)

    assert provider.client.kwargs["api_key"] == "gemini-test-key"
    assert provider.client.kwargs["base_url"] == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert provider.get_model("search") == "gemini-2.5-flash"


def test_grok_uses_xai_openai_compatible_endpoint(monkeypatch) -> None:
    settings = SimpleNamespace(
        ai_provider="grok",
        grok_api_key="grok-test-key",
        grok_model="grok-4",
    )
    values = {
        "ai_provider": "grok",
        "grok_api_key": "grok-test-key",
        "grok_model": "grok-4",
    }

    monkeypatch.setattr(core, "get_app_setting", lambda key, default=None: values.get(key, default))
    monkeypatch.setattr(ai, "OpenAI", FakeOpenAI)

    provider = AIProvider(settings)

    assert provider.client.kwargs["api_key"] == "grok-test-key"
    assert provider.client.kwargs["base_url"] == "https://api.x.ai/v1"
    assert provider.get_model("search") == "grok-4"


def test_groq_uses_groqcloud_openai_compatible_endpoint(monkeypatch) -> None:
    settings = SimpleNamespace(
        ai_provider="groq",
        groq_api_key="groq-test-key",
        groq_model="llama-3.3-70b-versatile",
    )
    values = {
        "ai_provider": "groq",
        "groq_api_key": "groq-test-key",
        "groq_model": "llama-3.3-70b-versatile",
    }

    monkeypatch.setattr(core, "get_app_setting", lambda key, default=None: values.get(key, default))
    monkeypatch.setattr(ai, "OpenAI", FakeOpenAI)

    provider = AIProvider(settings)

    assert provider.client.kwargs["api_key"] == "groq-test-key"
    assert provider.client.kwargs["base_url"] == "https://api.groq.com/openai/v1"
    assert provider.get_model("search") == "llama-3.3-70b-versatile"


def test_github_models_uses_github_inference_endpoint(monkeypatch) -> None:
    settings = SimpleNamespace(
        ai_provider="github",
        github_api_key="github-test-key",
        github_model="openai/gpt-4.1-mini",
    )
    values = {
        "ai_provider": "github",
        "github_api_key": "github-test-key",
        "github_model": "openai/gpt-4.1-mini",
    }

    monkeypatch.setattr(core, "get_app_setting", lambda key, default=None: values.get(key, default))
    monkeypatch.setattr(ai, "OpenAI", FakeOpenAI)

    provider = AIProvider(settings)

    assert provider.client.kwargs["api_key"] == "github-test-key"
    assert provider.client.kwargs["base_url"] == "https://models.github.ai/inference"
    assert provider.client.kwargs["default_headers"]["Accept"] == "application/vnd.github+json"
    assert provider.get_model("search") == "openai/gpt-4.1-mini"
