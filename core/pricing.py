"""モデルカタログ(単価・レイテンシ・品質)の唯一の情報源.

重要(正直表記):
  - 単価と model ID は **時間とともに必ず陳腐化する**。本ファイルはハードコードされた
    「初期値」であり、一次情報(各社の料金ページ)で検証したものではない。
  - `PRICING_SOURCE` / `PRICING_AS_OF` に出典と基準日を明示し、
    `AI_PLATFORM_PRICING_FILE` で外部 JSON に差し替え可能にしている。
  - コスト計算を根拠に意思決定する場合、必ず外部 JSON で実単価を注入すること。

以前は core/providers.py と core/real_providers.py に別々の spec が重複定義され、
同じ "claude" が "claude-sonnet" と "claude-3-5-sonnet" という異なる model 文字列を
持っていた(=メトリクスの model 別集計が mock/real で分裂する)。本ファイルに集約して解消する。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

PRICING_AS_OF = "unverified"   # 一次情報で検証した基準日を入れる(未検証のため空扱い)
PRICING_SOURCE = {
    "claude": "https://www.anthropic.com/pricing",
    "openai": "https://openai.com/api/pricing/",
    "gemini": "https://ai.google.dev/pricing",
}


@dataclass(frozen=True)
class ModelEntry:
    """1 プロバイダ分のカタログエントリ."""

    name: str                    # 論理プロバイダ名 (claude / openai / gemini)
    model: str                   # API に渡す model ID
    cost_per_1k_input: float     # USD / 1k input tokens
    cost_per_1k_output: float    # USD / 1k output tokens
    avg_latency_ms: float        # 実測 EMA で更新する想定の初期値
    quality_score: float         # 0.0-1.0 の相対品質(社内ベンチ想定)
    key_env: str                 # APIキーの環境変数名
    verified: bool = False       # 一次情報で単価を確認済みか

    def __post_init__(self) -> None:
        if self.cost_per_1k_input < 0 or self.cost_per_1k_output < 0:
            raise ValueError(f"{self.name}: cost must be >= 0")
        if not 0.0 <= self.quality_score <= 1.0:
            raise ValueError(f"{self.name}: quality_score must be within 0.0-1.0")
        if self.avg_latency_ms <= 0:
            raise ValueError(f"{self.name}: avg_latency_ms must be > 0")


# model ID は「日付サフィックス無しの別名」だと 404 になる提供元があるため、
# 各社が別名として公式に受け付ける -latest 形式を既定にしている。
DEFAULT_CATALOG: list[ModelEntry] = [
    ModelEntry("claude", "claude-3-5-sonnet-latest", 0.003, 0.015, 900, 0.93, "ANTHROPIC_API_KEY"),
    ModelEntry("openai", "gpt-4o", 0.0025, 0.010, 1100, 0.92, "OPENAI_API_KEY"),
    ModelEntry("gemini", "gemini-1.5-pro", 0.00125, 0.005, 1300, 0.88, "GOOGLE_API_KEY"),
]


def _from_json(path: str) -> list[ModelEntry]:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, list):
        raise ValueError(f"pricing file must be a JSON array: {path}")
    entries: list[ModelEntry] = []
    for item in raw:
        entries.append(ModelEntry(
            name=item["name"], model=item["model"],
            cost_per_1k_input=float(item["cost_per_1k_input"]),
            cost_per_1k_output=float(item["cost_per_1k_output"]),
            avg_latency_ms=float(item.get("avg_latency_ms", 1000)),
            quality_score=float(item.get("quality_score", 0.9)),
            key_env=item.get("key_env", f"{item['name'].upper()}_API_KEY"),
            verified=bool(item.get("verified", True)),
        ))
    return entries


def load_catalog(path: str | None = None) -> dict[str, ModelEntry]:
    """カタログを読み込む. AI_PLATFORM_PRICING_FILE があれば実単価で上書きする."""
    path = path or os.getenv("AI_PLATFORM_PRICING_FILE") or ""
    entries = _from_json(path) if path else DEFAULT_CATALOG
    return {e.name: e for e in entries}


def unverified_providers(catalog: dict[str, ModelEntry] | None = None) -> list[str]:
    """単価が未検証のプロバイダ名。UI/レポートで警告表示するために使う."""
    cat = catalog if catalog is not None else load_catalog()
    return sorted(n for n, e in cat.items() if not e.verified)
