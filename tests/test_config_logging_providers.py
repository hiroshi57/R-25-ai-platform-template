"""全機能拡張の検証: 設定 / ロギング / 実プロバイダ切替(mockフォールバック)."""
import json
import logging

from core import (
    MockProvider,
    RoutingStrategy,
    Settings,
    build_provider,
    build_providers,
    configure_logging,
    get_request_id,
    new_request_id,
    provider_mode,
)
from core.real_providers import AnthropicProvider


# --- Settings ---
def test_settings_defaults():
    s = Settings()
    assert s.default_strategy == RoutingStrategy.BALANCED
    assert "claude" in s.enabled_providers


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("AI_PLATFORM_STRATEGY", "cost")
    monkeypatch.setenv("AI_PLATFORM_PROVIDERS", "claude,openai")
    monkeypatch.setenv("AI_PLATFORM_RATE_CAPACITY", "50")
    s = Settings.from_env()
    assert s.default_strategy == RoutingStrategy.COST
    assert s.enabled_providers == ["claude", "openai"]
    assert s.rate_capacity == 50.0


def test_settings_invalid_strategy_falls_back(monkeypatch):
    monkeypatch.setenv("AI_PLATFORM_STRATEGY", "nonsense")
    assert Settings.from_env().default_strategy == RoutingStrategy.BALANCED


# --- プロバイダ切替(キー無し=mock, キーあり=real) ---
def test_provider_falls_back_to_mock_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    p = build_provider("claude")
    assert isinstance(p, MockProvider)
    assert provider_mode("claude") == "mock"


def test_provider_uses_real_class_with_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    p = build_provider("claude")
    assert isinstance(p, AnthropicProvider)   # 実APIクラスに切替
    assert provider_mode("claude") == "real"


def test_build_providers_all_mock_without_keys(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    provs = build_providers(["claude", "openai", "gemini"])
    assert set(provs) == {"claude", "openai", "gemini"}
    assert all(isinstance(p, MockProvider) for p in provs.values())


# --- 構造化ロギング + request_id ---
def test_request_id_set_and_get():
    rid = new_request_id()
    assert get_request_id() == rid
    assert len(rid) == 12


def test_json_logging_includes_request_id(capsys):
    configure_logging(level="INFO", json_format=True)
    new_request_id()
    logging.getLogger("test").info("hello")
    out = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(out)
    assert payload["msg"] == "hello"
    assert payload["request_id"] == get_request_id()
