"""LLM プロバイダ抽象と mock 実装.

差別化の土台: 各プロバイダは「コスト・レイテンシ・品質」のメタデータを持ち、
Router がこれを使って要求に最適なプロバイダを選ぶ。API キーが無くても
rule-based の MockProvider で動作する。

spec の定義は core/pricing.py に集約している(mock と real で model 文字列が
食い違うとメトリクスの model 別集計が分裂するため)。
"""
from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from typing import Callable

from .pricing import ModelEntry, load_catalog


@dataclass(frozen=True)
class ProviderSpec:
    """プロバイダの選択に使うメタデータ."""

    name: str                       # 例: "claude", "openai", "gemini"
    model: str
    cost_per_1k_input: float        # USD / 1k input tokens
    cost_per_1k_output: float       # USD / 1k output tokens
    avg_latency_ms: float           # 実測 EMA で更新される想定の初期値
    quality_score: float            # 0.0-1.0 の相対品質(社内ベンチ想定)

    @classmethod
    def from_entry(cls, e: ModelEntry) -> ProviderSpec:
        return cls(e.name, e.model, e.cost_per_1k_input, e.cost_per_1k_output,
                   e.avg_latency_ms, e.quality_score)

    def estimated_cost(self, input_tokens: int, output_tokens: int) -> float:
        # 負のトークン数は上流のバグ。0 に丸めるとコストが過小申告されるため明示的に弾く。
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("token counts must be >= 0")
        return (
            input_tokens / 1000 * self.cost_per_1k_input
            + output_tokens / 1000 * self.cost_per_1k_output
        )


@dataclass
class Completion:
    text: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cost_usd: float
    estimated_tokens: bool = False   # トークン数が推定値か(実 usage か)を明示


# --- トークン推定 -------------------------------------------------------------
# 旧実装は len(text.split()) * 1.3 だった。空白で区切らない日本語では
# 43文字の文が「2トークン」と推定され、実際の約 30-40 トークンに対して
# 15-20 倍の過小評価になっていた。コスト統制を売りにする以上これは致命的なので、
# 文字種別(CJK / ラテン)を分けて数える方式に変更する。
#
# 注意: これは依然として **近似** であり、正確なトークン数は各社トークナイザに依存する。
# 実 API 呼び出しでは必ずレスポンスの usage を優先して使うこと(下記 real_providers 参照)。
_CJK_RANGES = (
    (0x3040, 0x30FF),    # ひらがな・カタカナ
    (0x3400, 0x4DBF),    # CJK 拡張A
    (0x4E00, 0x9FFF),    # CJK 統合漢字
    (0xF900, 0xFAFF),    # CJK 互換漢字
    (0xAC00, 0xD7AF),    # ハングル
    (0xFF00, 0xFFEF),    # 全角形
)

# 経験則: CJK は 1 文字あたり約 0.9-1.1 トークン、ラテン系は 1 単語あたり約 1.3 トークン。
CJK_TOKENS_PER_CHAR = 1.0
LATIN_TOKENS_PER_WORD = 1.3


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def estimate_tokens(text: str) -> int:
    """外部依存なしの粗いトークン推定(CJK 対応).

    CJK 文字は 1 文字ずつ、それ以外は空白区切りの語数で数える。
    tiktoken 等が使える環境ではそちらを優先すべきだが、本テンプレートは
    標準ライブラリのみで動く事を要件としているため近似で済ませる。
    """
    if not text:
        return 1
    text = unicodedata.normalize("NFKC", text)
    cjk_chars = 0
    latin_buf = []
    for ch in text:
        if _is_cjk(ch):
            cjk_chars += 1
        else:
            latin_buf.append(ch)
    latin_words = len("".join(latin_buf).split())
    tokens = cjk_chars * CJK_TOKENS_PER_CHAR + latin_words * LATIN_TOKENS_PER_WORD
    return max(1, int(round(tokens)))


# 後方互換(旧名を import している外部コード向け)
_estimate_tokens = estimate_tokens


class BaseProvider:
    """全プロバイダの基底. spec を必ず持つ."""

    def __init__(self, spec: ProviderSpec) -> None:
        self.spec = spec

    def generate(self, prompt: str, max_output_tokens: int = 256) -> Completion:  # pragma: no cover
        raise NotImplementedError


class MockProvider(BaseProvider):
    """API キー無しで決定的に動く rule-based プロバイダ.

    latency は avg_latency_ms を中心に **決定的な擬似ジッタ** を載せる。
    旧実装は常に同一値を返していたため、p95 レイテンシが「常に平均と一致する」
    無意味な指標になり、観測性のデモとして誤解を招いていた。
    ジッタは prompt のハッシュから導出するため再現性は保たれる(テストは安定)。
    """

    def __init__(self, spec: ProviderSpec, responder: Callable[[str], str] | None = None,
                 jitter_ratio: float = 0.35) -> None:
        super().__init__(spec)
        self.jitter_ratio = jitter_ratio
        self._responder = responder or (
            lambda p: f"[{spec.name}:{spec.model}] response to: {p.strip()[:80]}")

    def _latency_for(self, prompt: str) -> float:
        if self.jitter_ratio <= 0:
            return self.spec.avg_latency_ms
        digest = hashlib.sha256(f"{self.spec.name}:{prompt}".encode()).digest()
        # 0.0-1.0 の決定的な値
        unit = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
        # 対数正規に近い右肩の重い分布を粗く再現(p95 が平均より明確に大きくなる)
        factor = 1.0 + self.jitter_ratio * (unit ** 3) * 4 - self.jitter_ratio * 0.2
        return round(max(1.0, self.spec.avg_latency_ms * factor), 1)

    def generate(self, prompt: str, max_output_tokens: int = 256) -> Completion:
        text = self._responder(prompt)
        input_tokens = estimate_tokens(prompt)
        output_tokens = min(estimate_tokens(text), max_output_tokens)
        return Completion(
            text=text,
            provider=self.spec.name,
            model=self.spec.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=self._latency_for(prompt),
            cost_usd=self.spec.estimated_cost(input_tokens, output_tokens),
            estimated_tokens=True,
        )


class FailingProvider(BaseProvider):
    """fallback 検証用: 常に例外を投げる."""

    def generate(self, prompt: str, max_output_tokens: int = 256) -> Completion:
        raise RuntimeError(f"provider {self.spec.name} is unavailable")


def default_specs() -> dict[str, ProviderSpec]:
    return {n: ProviderSpec.from_entry(e) for n, e in load_catalog().items()}


# 後方互換: 旧 DEFAULT_SPECS(リスト)を参照しているコード向け
DEFAULT_SPECS = list(default_specs().values())


def default_mock_providers() -> dict[str, BaseProvider]:
    return {name: MockProvider(spec) for name, spec in default_specs().items()}
