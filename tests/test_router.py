"""LLMRouter の選択・フォールバック・観測性の検証."""

from core import (
    FailingProvider,
    LLMRouter,
    MockProvider,
    NoProviderAvailable,
    ProviderSpec,
    RoutingStrategy,
    default_mock_providers,
)


def _spec(name, cost_in, cost_out, latency, quality):
    return ProviderSpec(name, f"{name}-model", cost_in, cost_out, latency, quality)


def _router():
    provs = {
        "cheap":  MockProvider(_spec("cheap", 0.0005, 0.001, 1500, 0.80)),
        "fast":   MockProvider(_spec("fast", 0.004, 0.012, 400, 0.85)),
        "premium": MockProvider(_spec("premium", 0.01, 0.03, 1000, 0.97)),
    }
    return LLMRouter(providers=provs)


def test_cost_strategy_picks_cheapest():
    r = _router()
    assert r.select(RoutingStrategy.COST).spec.name == "cheap"


def test_latency_strategy_picks_fastest():
    r = _router()
    assert r.select(RoutingStrategy.LATENCY).spec.name == "fast"


def test_quality_strategy_picks_highest_quality():
    r = _router()
    assert r.select(RoutingStrategy.QUALITY).spec.name == "premium"


def test_route_records_metric_and_returns_completion():
    r = _router()
    c = r.route("hello world", strategy=RoutingStrategy.COST)
    assert c.provider == "cheap"
    assert c.cost_usd > 0
    assert r.metrics.count == 1
    summary = r.metrics.summary()
    assert summary["count"] == 1
    assert summary["by_provider"]["cheap"]["count"] == 1


def test_fallback_when_primary_fails():
    provs = {
        "broken": FailingProvider(_spec("broken", 0.0001, 0.0001, 100, 0.9)),  # 最安&最速だが壊れている
        "backup": MockProvider(_spec("backup", 0.005, 0.01, 900, 0.9)),
    }
    r = LLMRouter(providers=provs)
    c = r.route("test", strategy=RoutingStrategy.COST)
    assert c.provider == "backup"
    summary = r.metrics.summary()
    # 1 論理リクエスト = 1 レコード. フォールバックしたので fallback_rate=1.0
    assert summary["count"] == 1
    assert summary["fallback_rate"] == 1.0


def test_all_providers_failing_raises():
    provs = {"a": FailingProvider(_spec("a", 1, 1, 1, 1)),
             "b": FailingProvider(_spec("b", 1, 1, 1, 1))}
    r = LLMRouter(providers=provs)
    try:
        r.route("x")
        raise AssertionError("should have raised")
    except NoProviderAvailable:
        pass


def test_default_providers_work_without_api_keys():
    r = LLMRouter(providers=default_mock_providers())
    c = r.route("no keys needed", strategy=RoutingStrategy.BALANCED)
    assert c.text
    assert c.provider in {"claude", "openai", "gemini"}
