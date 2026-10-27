"""コスト/レイテンシ/品質を考慮した LLM ルーター(本テンプレートの差別化コア).

- strategy に応じて登録済みプロバイダから最適な1つを選ぶ
- 呼び出しが失敗したら次点へ自動フォールバック
- 連続失敗したプロバイダはサーキットブレーカで一時的に降格
- 全呼び出しを MetricsStore に記録
"""
from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from enum import Enum

from .observability import MetricsStore, RequestMetric
from .providers import BaseProvider, Completion, default_mock_providers, estimate_tokens


class RoutingStrategy(str, Enum):
    COST = "cost"          # 最安を優先
    LATENCY = "latency"    # 最速を優先
    QUALITY = "quality"    # 最高品質を優先
    BALANCED = "balanced"  # コスト・レイテンシ・品質の合成スコア


class NoProviderAvailable(RuntimeError):
    pass


# BALANCED の重み(合計 1.0)。用途に応じて調整する。
DEFAULT_WEIGHTS = {"cost": 0.4, "latency": 0.3, "quality": 0.3}


def _min_max(values: Sequence[float], higher_is_better: bool) -> list[float]:
    """0.0-1.0 に min-max 正規化する.

    旧実装は「max で割る」方式だったため、値域が狭い指標(品質 0.88-0.93)は
    正規化後も狭いままで、名目の重み 0.3 に対して実効的な影響力が
    ほぼゼロ(スコア差 0.015)になっていた。min-max なら各指標が
    必ず 0.0-1.0 の全域を使うため、重みが意図どおりに効く。
    """
    lo, hi = min(values), max(values)
    span = hi - lo
    if span <= 0:
        # 全プロバイダが同値 -> この指標では優劣なし(中立の 0.5)
        return [0.5] * len(values)
    if higher_is_better:
        return [(v - lo) / span for v in values]
    return [1.0 - (v - lo) / span for v in values]


class _CircuitBreaker:
    """連続失敗したプロバイダを一定時間だけ降格させる.

    旧実装には無かったため、恒常的に落ちているプロバイダが
    毎リクエストで必ず最初に試され、全リクエストにタイムアウト分の
    レイテンシが上乗せされ続ける状態になっていた。
    """

    def __init__(self, threshold: int = 3, cooldown_sec: float = 30.0) -> None:
        self.threshold = threshold
        self.cooldown_sec = cooldown_sec
        self._fails: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def is_open(self, name: str, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        with self._lock:
            opened = self._opened_at.get(name)
            if opened is None:
                return False
            if now - opened >= self.cooldown_sec:
                # クールダウン満了 -> half-open(再試行を許す)
                self._opened_at.pop(name, None)
                self._fails[name] = 0
                return False
            return True

    def record_failure(self, name: str, now: float | None = None) -> None:
        now = now if now is not None else time.monotonic()
        with self._lock:
            n = self._fails.get(name, 0) + 1
            self._fails[name] = n
            if n >= self.threshold:
                self._opened_at[name] = now

    def record_success(self, name: str) -> None:
        with self._lock:
            self._fails[name] = 0
            self._opened_at.pop(name, None)


class LLMRouter:
    def __init__(
        self,
        providers: dict[str, BaseProvider] | None = None,
        metrics: MetricsStore | None = None,
        weights: dict[str, float] | None = None,
        breaker: _CircuitBreaker | None = None,
    ) -> None:
        self.providers: dict[str, BaseProvider] = providers or default_mock_providers()
        if not self.providers:
            raise ValueError("at least one provider is required")
        self.metrics = metrics or MetricsStore()
        self.weights = {**DEFAULT_WEIGHTS, **(weights or {})}
        total = sum(self.weights.values())
        if total <= 0:
            raise ValueError("weights must sum to a positive value")
        # 合計 1.0 に正規化(呼び出し側が任意の比率を渡せるように)
        self.weights = {k: v / total for k, v in self.weights.items()}
        self.breaker = breaker or _CircuitBreaker()

    # --- 選択ロジック ---
    def _rank(
        self,
        strategy: RoutingStrategy,
        input_tokens: int = 1000,
        output_tokens: int = 1000,
    ) -> list[BaseProvider]:
        """想定ワークロード(input/output トークン数)に基づいて並べる.

        旧実装は常に 1k in / 1k out 固定で単価を比較していた。実際には
        入力偏重(RAG)と出力偏重(生成)で最安プロバイダは入れ替わりうるため、
        実際のトークン数で評価する。
        """
        provs = list(self.providers.values())
        costs = [p.spec.estimated_cost(input_tokens, output_tokens) for p in provs]
        lats = [p.spec.avg_latency_ms for p in provs]
        quals = [p.spec.quality_score for p in provs]

        if strategy == RoutingStrategy.COST:
            keyed: list[tuple[float, BaseProvider]] = list(zip(costs, provs))
            ranked = [p for _, p in sorted(keyed, key=lambda kv: kv[0])]
        elif strategy == RoutingStrategy.LATENCY:
            ranked = [p for _, p in sorted(zip(lats, provs), key=lambda kv: kv[0])]
        elif strategy == RoutingStrategy.QUALITY:
            ranked = [p for _, p in sorted(zip(quals, provs),
                                           key=lambda kv: kv[0], reverse=True)]
        else:
            cost_n = _min_max(costs, higher_is_better=False)
            lat_n = _min_max(lats, higher_is_better=False)
            qual_n = _min_max(quals, higher_is_better=True)
            scored = [
                (self.weights["cost"] * c
                 + self.weights["latency"] * lat
                 + self.weights["quality"] * q, p)
                for c, lat, q, p in zip(cost_n, lat_n, qual_n, provs)
            ]
            ranked = [p for _, p in sorted(scored, key=lambda kv: kv[0], reverse=True)]

        # サーキットが開いているプロバイダは末尾へ回す(除外はしない=全滅を避ける)
        healthy = [p for p in ranked if not self.breaker.is_open(p.spec.name)]
        tripped = [p for p in ranked if self.breaker.is_open(p.spec.name)]
        return healthy + tripped

    def select(self, strategy: RoutingStrategy = RoutingStrategy.BALANCED) -> BaseProvider:
        ranked = self._rank(strategy)
        return ranked[0]

    # --- 実行(フォールバック付き) ---
    def route(
        self,
        prompt: str,
        strategy: RoutingStrategy = RoutingStrategy.BALANCED,
        max_output_tokens: int = 256,
    ) -> Completion:
        completion, _ = self.route_with_metric(prompt, strategy, max_output_tokens)
        return completion

    def route_with_metric(
        self,
        prompt: str,
        strategy: RoutingStrategy = RoutingStrategy.BALANCED,
        max_output_tokens: int = 256,
    ) -> tuple[Completion, RequestMetric]:
        """Completion と、それに対応する RequestMetric を返す.

        呼び出し側が「直近のメトリクス」を MetricsStore の内部リストから
        取り出す必要をなくすため(並行リクエスト下では別リクエストの
        レコードを掴んでしまい、テナントを取り違える)。
        """
        if max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be > 0")
        est_in = estimate_tokens(prompt)
        ranked = self._rank(strategy, input_tokens=est_in, output_tokens=max_output_tokens)
        last_error: Exception | None = None
        for idx, provider in enumerate(ranked):
            try:
                completion = provider.generate(prompt, max_output_tokens=max_output_tokens)
            except Exception as exc:  # noqa: BLE001 - 意図的に全捕捉して次点へ
                last_error = exc
                self.breaker.record_failure(provider.spec.name)
                continue
            self.breaker.record_success(provider.spec.name)
            # 1 論理リクエスト = 1 レコード. attempts に到達までの試行数を残す
            metric = RequestMetric(
                provider=completion.provider, model=completion.model,
                strategy=strategy.value, input_tokens=completion.input_tokens,
                output_tokens=completion.output_tokens, latency_ms=completion.latency_ms,
                cost_usd=completion.cost_usd, fell_back=(idx > 0), attempts=idx + 1, ok=True,
            )
            self.metrics.record(metric)
            return completion, metric
        # 全滅時は失敗レコードを 1 件残してから raise
        metric = RequestMetric(
            provider="none", model="none", strategy=strategy.value,
            input_tokens=0, output_tokens=0, latency_ms=0.0, cost_usd=0.0,
            fell_back=True, attempts=len(ranked), ok=False,
        )
        self.metrics.record(metric)
        raise NoProviderAvailable(
            f"all {len(ranked)} providers failed; last error: {last_error}") from last_error
