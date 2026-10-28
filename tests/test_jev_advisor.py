"""Jev によるルーティング戦略の助言（core/jev_advisor.py）のテスト。通信はしない."""
from __future__ import annotations

import json

import pytest

from core.jev_advisor import AdviceMode, StrategyAdvisor, parse_mode
from core.jev_client import JevError
from core.llm_router import RoutingStrategy

B, C, Q, L = RoutingStrategy.BALANCED, RoutingStrategy.COST, RoutingStrategy.QUALITY, RoutingStrategy.LATENCY


def _choice(probs: dict[str, float]) -> dict:
    top = max(probs, key=probs.get)
    return {"model": "jev-1.13.0", "usage": {"input_tokens": 50, "output_tokens": 10},
            "answers": {"tier": {"type": "choice", "choice": top, "probabilities": probs, "confidence": 0.8}}}


def _advisor(mode=AdviceMode.ON, resp=None, exc=None, calls=None, **kw):
    def post(payload):
        if calls is not None:
            calls.append(payload)
        if exc:
            raise exc
        return resp
    return StrategyAdvisor(post=post, mode=mode, **kw)


# --- コードで先に決めること（Jev に聞かない） -----------------------------------------

def test_off_mode_never_calls():
    calls = []
    a = _advisor(AdviceMode.OFF, calls=calls).advise("翻訳して: hello", B)
    assert a.used == B and calls == [] and a.suggested is None


@pytest.mark.parametrize("explicit", [C, Q, L])
def test_explicit_strategy_from_caller_is_respected(explicit):
    calls = []
    a = _advisor(calls=calls).advise("翻訳して", explicit)
    assert a.used == explicit and calls == [] and "指定" in a.reason


@pytest.mark.parametrize("prompt", [
    "連絡先は taro.yamada@example.com です。返信文を書いて",
    "電話 090-1234-5678 に折り返す文面",
    "カード 4111 1111 1111 1111 の明細を要約",
    'api_key = "sk-live-0123456789abcdef" を設定する手順',
    "-----BEGIN PRIVATE KEY----- の扱い",
])
def test_sensitive_prompts_are_never_sent(prompt):
    calls = []
    a = _advisor(calls=calls).advise(prompt, B)
    assert calls == [] and a.used == B and "送らない" in a.reason


def test_long_prompts_are_not_sent():
    calls = []
    a = _advisor(calls=calls, max_prompt_chars=20).advise("x" * 21, B)
    assert calls == [] and a.used == B


# --- Jev に聞くこと ----------------------------------------------------------------

def test_request_shape_is_one_choice_with_described_options():
    calls = []
    _advisor(resp=_choice({"cost": 0.9, "balanced": 0.1, "quality": 0.0}), calls=calls).advise("この文を英訳: こんにちは", B)
    q = calls[0]["questions"]["tier"]
    assert calls[0]["model"] == "jev-latest" and q["type"] == "choice"
    assert set(q["criteria"]) == {"cost", "balanced", "quality"} and all(q["criteria"].values())
    assert calls[0]["state"]["prompt"] == "この文を英訳: こんにちは"


def test_clear_advice_is_used_in_on_mode():
    a = _advisor(resp=_choice({"cost": 0.85, "balanced": 0.1, "quality": 0.05})).advise("英訳: おはよう", B)
    assert (a.suggested, a.used, a.adopted) == (C, C, True)


def test_unclear_advice_keeps_requested():
    a = _advisor(resp=_choice({"cost": 0.55, "balanced": 0.40, "quality": 0.05})).advise("説明して", B)
    assert a.suggested == C and a.used == B and a.adopted is False and "1位" in a.reason


def test_shadow_mode_records_but_does_not_change_strategy():
    a = _advisor(AdviceMode.SHADOW, resp=_choice({"quality": 0.9, "balanced": 0.1, "cost": 0.0})).advise(
        "このバグを直して", B)
    assert a.suggested == Q and a.used == B and a.mode == AdviceMode.SHADOW


@pytest.mark.parametrize("resp,exc", [
    (None, JevError("HTTP 529")),
    (None, TimeoutError()),
    ({"answers": {}}, None),
    ({"answers": {"tier": {"type": "choice", "choice": "unknown", "probabilities": {"unknown": 1.0}}}}, None),
])
def test_any_failure_keeps_requested(resp, exc):
    a = _advisor(resp=resp, exc=exc).advise("要約して", B)
    assert a.used == B and a.suggested is None and "失敗" in a.reason


def test_advice_log_never_contains_prompt_text(caplog):
    caplog.set_level("INFO")
    secret_like_but_allowed = "社外秘プロジェクト名アルファの議事録を要約"
    a = _advisor(resp=_choice({"cost": 0.9, "balanced": 0.1, "quality": 0.0})).advise(secret_like_but_allowed, B)
    assert secret_like_but_allowed not in json.dumps(a.as_log(), ensure_ascii=False)
    assert all(secret_like_but_allowed not in r.getMessage() for r in caplog.records)


def test_stats_count_outcomes():
    adv = _advisor(resp=_choice({"cost": 0.9, "balanced": 0.1, "quality": 0.0}))
    adv.advise("英訳: a", B)
    adv.advise("英訳: b", C)            # 呼び出し側が指定
    s = adv.stats()
    assert s["asked"] == 1 and s["adopted"] == 1 and s["skipped"] == 1


# --- 設定 --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [("off", AdviceMode.OFF), ("SHADOW", AdviceMode.SHADOW),
                                          (" on ", AdviceMode.ON), ("", AdviceMode.OFF),
                                          ("yes", AdviceMode.OFF)])
def test_parse_mode(raw, expected):
    assert parse_mode(raw) == expected


# --- /v1/chat への組み込み ----------------------------------------------------------

@pytest.mark.parametrize("mode,expected", [(AdviceMode.ON, "cost"), (AdviceMode.SHADOW, "balanced")])
def test_chat_endpoint_uses_advice_only_in_on_mode(monkeypatch, auth_headers, mode, expected):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    import service.api as api
    monkeypatch.setattr(api, "ADVISOR", _advisor(mode, resp=_choice({"cost": 0.9, "balanced": 0.1, "quality": 0.0})))
    c = TestClient(api.create_app())
    r = c.post("/v1/chat", json={"prompt": "英訳して: ありがとう", "strategy": "balanced"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["strategy"] == expected
    assert body["routing_advice"]["suggested"] == "cost" and body["routing_advice"]["mode"] == mode.value
    assert "ありがとう" not in json.dumps(body["routing_advice"], ensure_ascii=False)


def test_chat_endpoint_has_no_advice_field_when_off(monkeypatch, auth_headers):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    import service.api as api
    monkeypatch.setattr(api, "ADVISOR", _advisor(AdviceMode.OFF))
    r = TestClient(api.create_app()).post("/v1/chat", json={"prompt": "x", "strategy": "balanced"},
                                          headers=auth_headers)
    assert r.status_code == 200 and "routing_advice" not in r.json()
