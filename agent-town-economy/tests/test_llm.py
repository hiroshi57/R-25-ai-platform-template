"""LLM policy: parsing, validation, fallback, and an end-to-end run with a fake
backend (no network). The real OpenAICompatibleBackend is exercised only for
request construction via a monkeypatched urlopen."""
import io
import json
import random

from sim.conditions import baseline
from sim.engine import Simulation
from sim.policy import HeuristicPolicy, LLMPolicy, _parse_tool_name
from analysis.validate import validate


def test_parse_json_tool():
    assert _parse_tool_name('{"tool": "buy_food"}') == "buy_food"


def test_parse_bare_token():
    assert _parse_tool_name("I will start_shift now") == "start_shift"


def test_parse_unknown_returns_none():
    assert _parse_tool_name('{"tool": "teleport"}') is None
    assert _parse_tool_name("nonsense words only") is None


def test_llm_policy_uses_backend():
    policy = LLMPolicy(backend=lambda prompt: '{"tool": "observe_place"}')

    class A:  # minimal duck-typed agent
        agent_id = 0
        owns_business = None
        on_shift_at = None
        in_conversations = 0
        pos = (0.0, 0.0)

    name, _ = policy.choose(A(), sim=None)
    assert name == "observe_place"


def test_llm_policy_falls_back_on_malformed():
    fallback = HeuristicPolicy(random.Random(0))
    policy = LLMPolicy(backend=lambda prompt: "garbage", fallback=fallback)

    class A:
        agent_id = 0
        owns_business = None
        on_shift_at = None
        in_conversations = 0
        pos = (0.0, 0.0)

    name, _ = policy.choose(A(), sim=None)
    assert name in __import__("sim.tools", fromlist=["TOOL_CATEGORIES"]).TOOL_CATEGORIES
    assert policy.malformed == 1


def test_end_to_end_run_with_fake_llm_backend():
    calls = {"n": 0}

    def backend(prompt):
        calls["n"] += 1
        # cycle through a couple of valid economic actions
        return '{"tool": "buy_food"}' if calls["n"] % 2 else '{"tool": "start_shift"}'

    policy = LLMPolicy(backend, fallback=HeuristicPolicy(random.Random(1)))
    sim = Simulation(baseline(), policy, seed=1, n_agents=8)
    result = sim.run(20)
    assert validate(result)["all_passed"]
    assert calls["n"] > 0


def test_openai_backend_builds_request(monkeypatch):
    from sim import llm_backend

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = req.headers
        captured["body"] = json.loads(req.data.decode("utf-8"))
        payload = {"choices": [{"message": {"content": '{"tool": "buy_food"}'}}]}
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(llm_backend.urllib.request, "urlopen", fake_urlopen)
    backend = llm_backend.OpenAICompatibleBackend(
        base_url="https://example.test/v1", model="test-model", api_key="sk-test"
    )
    out = backend("hello")
    assert out == '{"tool": "buy_food"}'
    assert captured["url"].endswith("/chat/completions")
    assert captured["body"]["model"] == "test-model"
    assert captured["headers"].get("Authorization") == "Bearer sk-test"
