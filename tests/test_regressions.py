"""レビューで発見した不具合の回帰テスト.

各テストは「修正前に落ちること」を確認したうえで追加している。
"""
from __future__ import annotations

import pytest

from core import (
    APIKeyStore,
    BudgetGuard,
    LLMRouter,
    MockProvider,
    NoProviderAvailable,
    ProviderSpec,
    RequestMetric,
    RoutingStrategy,
    Settings,
    build_providers,
    days_in_month_of,
    detect_cost_anomaly,
    estimate_tokens,
    project_month_end,
    provider_mode,
    summarize,
)
from core.llm_router import _CircuitBreaker, _min_max
from core.providers import FailingProvider


def _spec(name, cost_in, cost_out, latency, quality):
    return ProviderSpec(name, f"{name}-model", cost_in, cost_out, latency, quality)


# --- トークン推定: 日本語 ------------------------------------------------------
def test_japanese_tokens_are_not_undercounted():
    """旧実装は空白区切りの語数だったため、日本語43文字が2トークンと推定されていた."""
    jp = "社内規程のうち経費精算の締め日を教えてください。至急確認したいので詳細をお願いします。"
    est = estimate_tokens(jp)
    # 実トークン数はトークナイザ依存だが、文字数の半分を下回るのは明らかに過小
    assert est >= len(jp) * 0.5, f"日本語のトークン推定が過小: {est} for {len(jp)} chars"


def test_english_tokens_still_word_based():
    en = "Please tell me the expense report submission deadline."
    assert 5 <= estimate_tokens(en) <= 15


def test_mixed_language_tokens():
    assert estimate_tokens("経費 report の deadline は?") > estimate_tokens("report deadline")


def test_empty_prompt_has_minimum_one_token():
    assert estimate_tokens("") == 1


def test_cost_scales_with_japanese_length():
    """入力コストが日本語の長さに比例すること(旧実装ではほぼ一定だった).

    旧実装では "非常に長い日本語のプロンプト"*20 (280文字) も空白が無いため
    1 語 = 2 トークン扱いになり、短文とコストがほとんど変わらなかった。
    """
    spec = _spec("x", 0.003, 0.015, 900, 0.9)
    short_in = spec.estimated_cost(estimate_tokens("短い"), 0)
    long_in = spec.estimated_cost(estimate_tokens("非常に長い日本語のプロンプト" * 20), 0)
    assert long_in > short_in * 50


# --- BALANCED スコアの正規化 ---------------------------------------------------
def test_min_max_gives_full_range():
    assert _min_max([0.88, 0.92, 0.93], higher_is_better=True) == pytest.approx([0.0, 0.8, 1.0])


def test_min_max_identical_values_are_neutral():
    assert _min_max([5.0, 5.0], higher_is_better=True) == [0.5, 0.5]


def test_quality_weight_actually_influences_balanced_routing():
    """旧実装は max 除算だったため、品質差 0.88-0.93 がスコアにほぼ影響しなかった.

    コスト・レイテンシが同一なら、品質だけで勝敗が決まらなければならない。
    """
    provs = {
        "low": MockProvider(_spec("low", 0.001, 0.001, 1000, 0.70)),
        "high": MockProvider(_spec("high", 0.001, 0.001, 1000, 0.99)),
    }
    r = LLMRouter(providers=provs)
    assert r.select(RoutingStrategy.BALANCED).spec.name == "high"


def test_balanced_weights_are_configurable_and_normalized():
    r = LLMRouter(providers={"a": MockProvider(_spec("a", 1, 1, 1, 1))},
                  weights={"cost": 2, "latency": 1, "quality": 1})
    assert sum(r.weights.values()) == pytest.approx(1.0)
    assert r.weights["cost"] == pytest.approx(0.5)


# --- ワークロードに応じたコストランキング -------------------------------------
def test_cost_ranking_reflects_output_heavy_workload():
    """入力単価が安く出力単価が高いモデルと、その逆で順位が入れ替わること."""
    provs = {
        "cheap_in": MockProvider(_spec("cheap_in", 0.0001, 0.100, 900, 0.9)),
        "cheap_out": MockProvider(_spec("cheap_out", 0.100, 0.0001, 900, 0.9)),
    }
    r = LLMRouter(providers=provs)
    # 出力偏重(長い出力を要求)
    ranked_out = r._rank(RoutingStrategy.COST, input_tokens=10, output_tokens=5000)
    assert ranked_out[0].spec.name == "cheap_out"
    # 入力偏重
    ranked_in = r._rank(RoutingStrategy.COST, input_tokens=5000, output_tokens=10)
    assert ranked_in[0].spec.name == "cheap_in"


# --- route_with_metric(テナント取り違え防止) ---------------------------------
def test_route_with_metric_returns_matching_record():
    """旧実装は metrics._metrics[-1] を掴んでいたため並行時に取り違えた."""
    r = LLMRouter(providers={"a": MockProvider(_spec("a", 0.001, 0.002, 500, 0.9))})
    completion, metric = r.route_with_metric("hello", strategy=RoutingStrategy.COST)
    assert metric.provider == completion.provider
    assert metric.cost_usd == completion.cost_usd
    assert metric.input_tokens == completion.input_tokens


def test_metrics_store_is_bounded():
    from core.observability import MetricsStore
    store = MetricsStore(max_records=10)
    for _ in range(50):
        store.record(RequestMetric("a", "m", "cost", 1, 1, 1.0, 0.1))
    assert store.count == 10       # 無制限に伸びない


def test_summarize_excludes_failures_from_latency():
    metrics = [RequestMetric("a", "m", "cost", 1, 1, 1000.0, 0.1) for _ in range(9)]
    metrics.append(RequestMetric("none", "none", "cost", 0, 0, 0.0, 0.0, True, 2, False))
    s = summarize(metrics)
    assert s["p95_latency_ms"] == 1000.0
    assert s["error_rate"] == 0.1
    assert "none" not in s["by_provider"]


# --- サーキットブレーカ --------------------------------------------------------
def test_circuit_breaker_opens_and_recovers():
    cb = _CircuitBreaker(threshold=2, cooldown_sec=10)
    assert cb.is_open("x", now=0) is False
    cb.record_failure("x", now=0)
    cb.record_failure("x", now=1)
    assert cb.is_open("x", now=2) is True
    assert cb.is_open("x", now=100) is False     # クールダウン後は half-open


def test_failing_provider_is_demoted_after_threshold():
    provs = {
        "broken": FailingProvider(_spec("broken", 0.0001, 0.0001, 100, 0.9)),
        "backup": MockProvider(_spec("backup", 0.005, 0.010, 900, 0.9)),
    }
    r = LLMRouter(providers=provs, breaker=_CircuitBreaker(threshold=2, cooldown_sec=60))
    for _ in range(3):
        r.route("x", strategy=RoutingStrategy.COST)
    # 3回目以降、broken はランキング末尾へ回る
    ranked = r._rank(RoutingStrategy.COST)
    assert ranked[0].spec.name == "backup"


def test_all_providers_failing_still_raises():
    provs = {"a": FailingProvider(_spec("a", 1, 1, 1, 1))}
    r = LLMRouter(providers=provs)
    with pytest.raises(NoProviderAvailable):
        r.route("x")


# --- FinOps -------------------------------------------------------------------
def test_days_in_month_is_calendar_accurate():
    from datetime import date
    assert days_in_month_of(date(2024, 2, 1)) == 29      # 閏年
    assert days_in_month_of(date(2023, 2, 1)) == 28
    assert days_in_month_of(date(2024, 1, 1)) == 31
    assert days_in_month_of(date(2024, 4, 1)) == 30


def test_project_month_end_rejects_impossible_day():
    with pytest.raises(ValueError):
        project_month_end(10.0, day_of_month=32, days_in_month=31)


def test_project_month_end_rejects_negative_spend():
    with pytest.raises(ValueError):
        project_month_end(-1.0, 10, 30)


def test_budget_guard_rejects_non_positive_budget():
    """旧実装は budget_usd=0 で ratio=0 -> 常に 'ok' を返し「守れている」と誤認させた."""
    with pytest.raises(ValueError):
        BudgetGuard(0)
    with pytest.raises(ValueError):
        BudgetGuard(-5)


def test_budget_guard_rejects_inverted_thresholds():
    with pytest.raises(ValueError):
        BudgetGuard(100, warn_ratio=1.5, critical_ratio=1.0)


def test_low_confidence_does_not_downgrade_strategy():
    """月初1日のスパイクで月中ずっと最安モデルに固定されるのを防ぐ."""
    g = BudgetGuard(budget_usd=100)
    # 1日目に$50 -> 着地$1500(critical)だがサンプル1日なので降格しない
    assert g.choose_strategy(50, 1, RoutingStrategy.QUALITY, 30) == RoutingStrategy.QUALITY
    # 10日目に$40 -> 着地$120(critical)、サンプル十分なので降格する
    assert g.choose_strategy(40, 10, RoutingStrategy.QUALITY, 30) == RoutingStrategy.COST


def test_budget_status_exposes_confidence_and_burn_rate():
    s = BudgetGuard(100).status(40, 10, 30)
    assert s.confidence == "high"
    assert s.burn_rate_ratio == pytest.approx(1.2)
    assert s.daily_avg_usd == pytest.approx(4.0)


def test_over_budget_when_already_spent_beyond_budget():
    """着地見込みだけでなく、実支出が既に超過している場合も検出する."""
    s = BudgetGuard(100).status(150, 30, 30)
    assert s.over_budget is True
    assert s.remaining_usd == 0.0


def test_anomaly_absolute_floor_suppresses_noise():
    """ベースラインが極小のとき、比率だけでアラートを出すとノイズになる."""
    noisy = detect_cost_anomaly([0.001, 0.001], today=0.01, threshold=2.0,
                                absolute_floor_usd=1.0)
    assert noisy.is_anomaly is False
    real = detect_cost_anomaly([1.0, 1.0], today=10.0, threshold=2.0,
                               absolute_floor_usd=1.0)
    assert real.is_anomaly is True


def test_anomaly_threshold_must_exceed_one():
    with pytest.raises(ValueError):
        detect_cost_anomaly([1.0], today=1.0, threshold=1.0)


# --- 設定の堅牢性 --------------------------------------------------------------
def test_invalid_float_env_does_not_crash(monkeypatch):
    """旧実装は float('abc') で ValueError -> app=None で原因不明の起動失敗."""
    monkeypatch.setenv("AI_PLATFORM_RATE_CAPACITY", "not-a-number")
    assert Settings.from_env().rate_capacity == 20.0


def test_invalid_log_level_falls_back(monkeypatch):
    monkeypatch.setenv("AI_PLATFORM_LOG_LEVEL", "VERBOSE")
    assert Settings.from_env().log_level == "INFO"


def test_unknown_provider_is_filtered_not_crashing(monkeypatch):
    """旧実装は build_providers が黙って除外し /v1/providers が KeyError -> 500."""
    monkeypatch.setenv("AI_PLATFORM_PROVIDERS", "claude,not-a-provider")
    s = Settings.from_env()
    assert s.enabled_providers == ["claude"]
    assert provider_mode("not-a-provider") == "unknown"    # 例外ではなく unknown


def test_all_unknown_providers_falls_back_to_catalog(monkeypatch):
    monkeypatch.setenv("AI_PLATFORM_PROVIDERS", "nope,nada")
    assert len(Settings.from_env().enabled_providers) > 0


def test_build_providers_raises_on_all_unknown():
    with pytest.raises(ValueError):
        build_providers(["nope"])


# --- 認証 ---------------------------------------------------------------------
def test_api_key_containing_colon_is_parsed(monkeypatch):
    """旧実装は最初の ':' で分割していたため、':' を含むキーが壊れた."""
    monkeypatch.setenv("AI_PLATFORM_API_KEYS", "abc:def:ghi:tenant-x")
    store = APIKeyStore.from_env()
    assert store.resolve_tenant("abc:def:ghi") == "tenant-x"


def test_malformed_api_key_entries_are_skipped(monkeypatch):
    monkeypatch.setenv("AI_PLATFORM_API_KEYS", "no-colon-here,,valid-key-1234567890:t1")
    store = APIKeyStore.from_env()
    assert store.resolve_tenant("valid-key-1234567890") == "t1"
    assert len(store) == 1


def test_empty_api_key_is_rejected():
    store = APIKeyStore({"real-key-1234567890": "t"})
    assert store.resolve_tenant("") is None
    assert store.resolve_tenant(None) is None


def test_add_rejects_empty_values():
    store = APIKeyStore()
    with pytest.raises(ValueError):
        store.add("", "tenant")
    with pytest.raises(ValueError):
        store.add("key-1234567890123456789012", "")


# --- 単価カタログの一貫性 ------------------------------------------------------
def test_mock_and_real_share_the_same_model_id():
    """旧実装は mock が 'claude-sonnet'、real が 'claude-3-5-sonnet' で
    メトリクスの model 別集計が分裂していた."""
    from core.providers import default_specs
    from core.real_providers import _registry
    mock_specs = default_specs()
    real_registry = _registry()
    for name, spec in mock_specs.items():
        assert spec.model == real_registry[name]["spec"].model


def test_catalog_rejects_invalid_entries():
    from core.pricing import ModelEntry
    with pytest.raises(ValueError):
        ModelEntry("x", "m", -1.0, 0.01, 900, 0.9, "K")       # 負の単価
    with pytest.raises(ValueError):
        ModelEntry("x", "m", 0.01, 0.01, 900, 1.5, "K")       # 品質 > 1.0
    with pytest.raises(ValueError):
        ModelEntry("x", "m", 0.01, 0.01, 0, 0.9, "K")         # レイテンシ 0


def test_pricing_file_override(tmp_path, monkeypatch):
    """実単価を外部 JSON で注入できること."""
    import json
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps([{
        "name": "claude", "model": "custom-model",
        "cost_per_1k_input": 0.001, "cost_per_1k_output": 0.002,
        "key_env": "ANTHROPIC_API_KEY", "verified": True,
    }]), encoding="utf-8")
    monkeypatch.setenv("AI_PLATFORM_PRICING_FILE", str(p))
    from core.pricing import load_catalog, unverified_providers
    cat = load_catalog()
    assert cat["claude"].model == "custom-model"
    assert unverified_providers(cat) == []


def test_default_catalog_is_flagged_unverified():
    """既定単価が「検証済み」を騙らないこと."""
    from core.pricing import unverified_providers
    assert set(unverified_providers()) == {"claude", "openai", "gemini"}


# --- 入力バリデーション --------------------------------------------------------
def test_negative_tokens_are_rejected():
    spec = _spec("x", 0.003, 0.015, 900, 0.9)
    with pytest.raises(ValueError):
        spec.estimated_cost(-1, 10)


def test_zero_max_output_tokens_rejected():
    r = LLMRouter(providers={"a": MockProvider(_spec("a", 0.001, 0.001, 100, 0.9))})
    with pytest.raises(ValueError):
        r.route("x", max_output_tokens=0)


# --- mock のレイテンシ分布 -----------------------------------------------------
def test_mock_latency_has_deterministic_spread():
    """旧実装は常に avg_latency_ms を返すため p95 が平均と一致し無意味だった."""
    p = MockProvider(_spec("a", 0.001, 0.001, 1000, 0.9))
    lats = {p.generate(f"prompt-{i}").latency_ms for i in range(50)}
    assert len(lats) > 1                       # 分布がある
    # 決定的: 同じ入力なら同じ値
    assert p.generate("same").latency_ms == p.generate("same").latency_ms


# --- app_template のエンドポイント(旧実装は常に 422 で全く動かなかった) --------
def test_app_template_chat_endpoint_works(monkeypatch):
    """回帰テスト: README のクイックスタートで案内される主要エンドポイント.

    旧実装は `from __future__ import annotations` + create_app() 内の
    ローカル Pydantic モデルという組み合わせで、FastAPI がボディを
    クエリパラメータと誤認し **すべてのリクエストが 422** になっていた。
    create_app() が `# pragma: no cover` で一切テストされていなかったため
    見逃されていた。
    """
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    import app_template.main as m
    m.API_KEYS.add("regression-key-0123456789abcdef", "regression-tenant")
    c = TestClient(m.create_app())
    key = {"X-API-Key": "regression-key-0123456789abcdef"}

    r = c.post("/v1/chat", json={"prompt": "経費精算の締め日は?", "strategy": "cost"}, headers=key)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"] and body["provider"]
    assert body["tenant"] == "regression-tenant"
    assert r.headers.get("x-request-id")

    # 認証必須
    assert c.post("/v1/chat", json={"prompt": "x"}).status_code == 401
    # メトリクスはテナント別(旧実装は全テナント合計を返していた)
    assert c.get("/v1/metrics", headers=key).json()["count"] == 1
    assert c.get("/healthz").status_code == 200


def test_app_template_has_no_hardcoded_demo_key(monkeypatch):
    """旧実装は demo-key を無条件登録し、本番でもバックドアになっていた."""
    import app_template.main as m
    assert m.API_KEYS.resolve_tenant("demo-key") is None
