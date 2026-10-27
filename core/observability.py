"""観測性: リクエスト単位でコスト・レイテンシ・トークンを記録し集計する.

新規 AI アプリがこのテンプレートを clone するだけで
「プロバイダ別コスト」「p95 レイテンシ」「フォールバック発生率」「エラー率」を
すぐ可視化できる。外部監視サービス非依存。
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass

logger = logging.getLogger("ai_platform.observability")

# 無制限に貯めるとプロセスが OOM するため既定で上限を設ける。
DEFAULT_MAX_RECORDS = 100_000


@dataclass
class RequestMetric:
    """1 論理リクエスト = 1 レコード. 途中のフォールバック試行は attempts に集約する
    (失敗試行を別レコードにすると count / コスト / p95 が歪むため)."""

    provider: str
    model: str
    strategy: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cost_usd: float
    fell_back: bool = False   # 一次プロバイダ以外で成功したか
    attempts: int = 1         # 成功までに試したプロバイダ数
    ok: bool = True


def percentile(values: Iterable[float], pct: float) -> float:
    """線形補間つきパーセンタイル(numpy 非依存).

    service/db.py にも同じ計算がコピーされていたため、こちらに集約して共有する。
    """
    ordered = sorted(values)
    if not ordered:
        return 0.0
    if not 0.0 <= pct <= 100.0:
        raise ValueError("pct must be within 0-100")
    if len(ordered) == 1:
        return float(ordered[0])
    k = (len(ordered) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    frac = k - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


# 後方互換
_percentile = percentile


def empty_summary() -> dict:
    return {"count": 0, "ok_count": 0, "error_count": 0, "error_rate": 0.0,
            "total_cost_usd": 0.0, "p50_latency_ms": 0.0, "p95_latency_ms": 0.0,
            "p99_latency_ms": 0.0, "fallback_rate": 0.0, "by_provider": {}}


def summarize(metrics: list[RequestMetric]) -> dict:
    """RequestMetric のリストを集計する(MetricsStore / DB 双方から使える純関数).

    重要な修正: 全プロバイダ失敗時のレコードは latency_ms=0 / cost=0 で記録されるため、
    これをレイテンシ分布に含めると p95 が実態より小さく出る(SLO を誤認する)。
    レイテンシ統計は ok=True のレコードのみで計算し、失敗は error_rate として別建てする。
    同様に by_provider の "none" 疑似プロバイダも成功集計から除外する。
    """
    total = len(metrics)
    if total == 0:
        return empty_summary()
    ok_metrics = [m for m in metrics if m.ok]
    latencies = [m.latency_ms for m in ok_metrics]
    by_provider: dict[str, dict] = {}
    for m in ok_metrics:
        bp = by_provider.setdefault(
            m.provider, {"count": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0})
        bp["count"] += 1
        bp["cost_usd"] = round(bp["cost_usd"] + m.cost_usd, 6)
        bp["input_tokens"] += m.input_tokens
        bp["output_tokens"] += m.output_tokens
    error_count = total - len(ok_metrics)
    return {
        "count": total,
        "ok_count": len(ok_metrics),
        "error_count": error_count,
        "error_rate": round(error_count / total, 4),
        "total_cost_usd": round(sum(m.cost_usd for m in metrics), 6),
        "p50_latency_ms": round(percentile(latencies, 50), 1),
        "p95_latency_ms": round(percentile(latencies, 95), 1),
        "p99_latency_ms": round(percentile(latencies, 99), 1),
        "fallback_rate": round(sum(1 for m in metrics if m.fell_back) / total, 4),
        "by_provider": by_provider,
    }


class MetricsStore:
    """インメモリのメトリクス集計。プロセス横断が必要なら差し替え可能.

    注意(正直表記): プロセスローカルかつ揮発性。複数インスタンスで動かすと
    インスタンスごとに別々の数字になる。恒久保存は service/db.py 側を使うこと。
    """

    def __init__(self, max_records: int = DEFAULT_MAX_RECORDS) -> None:
        self._metrics: deque[RequestMetric] = deque(maxlen=max_records)
        self._lock = threading.Lock()

    def record(self, metric: RequestMetric) -> None:
        with self._lock:
            self._metrics.append(metric)
        logger.info(
            "llm_call provider=%s model=%s strategy=%s cost=%.6f latency_ms=%.1f "
            "fell_back=%s attempts=%d ok=%s",
            metric.provider, metric.model, metric.strategy,
            metric.cost_usd, metric.latency_ms, metric.fell_back, metric.attempts, metric.ok,
        )

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._metrics)

    def snapshot(self) -> list[RequestMetric]:
        with self._lock:
            return list(self._metrics)

    def last(self) -> RequestMetric | None:
        with self._lock:
            return self._metrics[-1] if self._metrics else None

    def reset(self) -> None:
        with self._lock:
            self._metrics.clear()

    def summary(self) -> dict:
        return summarize(self.snapshot())
