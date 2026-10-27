"""実プロバイダ切替(Claude/GPT/Gemini). SDK は遅延 import.

APIキーが無い / SDK 未インストールの場合は MockProvider へ自動フォールバックするため、
clone 直後(キー無し)でも動作し、キーを設定すれば実 API に切り替わる。

重要:
  - すべての実クライアントに **タイムアウト** を設定する。タイムアウトが無いと
    ハングしたプロバイダで接続が張り付き、Router のフォールバックが発火しない
    (フォールバックは例外が上がって初めて動くため)。
  - トークン数はレスポンスの usage を優先し、取得できない場合のみ推定にフォールバックする。
    推定値を使ったかどうかは Completion.estimated_tokens で判別できる。
"""
from __future__ import annotations

import logging
import os
import time

from .pricing import load_catalog
from .providers import (
    BaseProvider,
    Completion,
    MockProvider,
    ProviderSpec,
    estimate_tokens,
)

logger = logging.getLogger("ai_platform.providers")

# 1 リクエストあたりの上限秒数。フォールバック込みでも SLO に収まる値にする。
DEFAULT_TIMEOUT_SEC = float(os.getenv("AI_PLATFORM_PROVIDER_TIMEOUT_SEC", "30"))
DEFAULT_MAX_RETRIES = int(os.getenv("AI_PLATFORM_PROVIDER_MAX_RETRIES", "1"))


def _registry() -> dict[str, dict]:
    """カタログから {name: {"spec":..., "key_env":...}} を組み立てる."""
    return {
        name: {"spec": ProviderSpec.from_entry(e), "key_env": e.key_env}
        for name, e in load_catalog().items()
    }


# 後方互換(旧 PROVIDER_REGISTRY を参照するコード向け)
PROVIDER_REGISTRY = _registry()


def _usage_int(usage, attr: str, fallback: int) -> tuple:
    """usage から実トークン数を取り出す。取れなければ推定値と estimated=True を返す."""
    val = getattr(usage, attr, None) if usage is not None else None
    if isinstance(val, int) and val >= 0:
        return val, False
    return fallback, True


class AnthropicProvider(BaseProvider):
    def generate(self, prompt: str, max_output_tokens: int = 256) -> Completion:  # pragma: no cover
        from anthropic import Anthropic  # 遅延 import
        client = Anthropic(timeout=DEFAULT_TIMEOUT_SEC, max_retries=DEFAULT_MAX_RETRIES)
        t0 = time.perf_counter()
        resp = client.messages.create(
            model=self.spec.model, max_tokens=max_output_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        latency = (time.perf_counter() - t0) * 1000
        text = "".join(getattr(b, "text", "") for b in resp.content
                       if getattr(b, "type", "") == "text")
        usage = getattr(resp, "usage", None)
        it, est_i = _usage_int(usage, "input_tokens", estimate_tokens(prompt))
        ot, est_o = _usage_int(usage, "output_tokens", estimate_tokens(text))
        return Completion(text, self.spec.name, self.spec.model, it, ot, latency,
                          self.spec.estimated_cost(it, ot), estimated_tokens=est_i or est_o)


class OpenAIProvider(BaseProvider):
    def generate(self, prompt: str, max_output_tokens: int = 256) -> Completion:  # pragma: no cover
        from openai import OpenAI
        client = OpenAI(timeout=DEFAULT_TIMEOUT_SEC, max_retries=DEFAULT_MAX_RETRIES)
        t0 = time.perf_counter()
        kwargs = {
            "model": self.spec.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            resp = client.chat.completions.create(max_completion_tokens=max_output_tokens, **kwargs)
        except TypeError:
            # 古い SDK は max_completion_tokens を知らない
            resp = client.chat.completions.create(max_tokens=max_output_tokens, **kwargs)
        latency = (time.perf_counter() - t0) * 1000
        choices = getattr(resp, "choices", None) or []
        if not choices:
            # 空レスポンスをそのまま通すと text="" が正常系として記録される
            raise RuntimeError("openai returned no choices")
        text = choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        it, est_i = _usage_int(usage, "prompt_tokens", estimate_tokens(prompt))
        ot, est_o = _usage_int(usage, "completion_tokens", estimate_tokens(text))
        return Completion(text, self.spec.name, self.spec.model, it, ot, latency,
                          self.spec.estimated_cost(it, ot), estimated_tokens=est_i or est_o)


class GeminiProvider(BaseProvider):
    def generate(self, prompt: str, max_output_tokens: int = 256) -> Completion:  # pragma: no cover
        import google.generativeai as genai
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY is not set")
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(self.spec.model)
        t0 = time.perf_counter()
        # 旧実装は max_output_tokens を一切渡していなかったため、上限指定が
        # 無視され想定外に長い(=高額な)応答が返りうる状態だった。
        resp = model.generate_content(
            prompt,
            generation_config={"max_output_tokens": max_output_tokens},
            request_options={"timeout": DEFAULT_TIMEOUT_SEC},
        )
        latency = (time.perf_counter() - t0) * 1000
        # セーフティフィルタでブロックされると resp.text は例外を投げる
        try:
            text = resp.text or ""
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"gemini returned no usable text: {exc}") from exc
        usage = getattr(resp, "usage_metadata", None)
        it, est_i = _usage_int(usage, "prompt_token_count", estimate_tokens(prompt))
        ot, est_o = _usage_int(usage, "candidates_token_count", estimate_tokens(text))
        return Completion(text, self.spec.name, self.spec.model, it, ot, latency,
                          self.spec.estimated_cost(it, ot), estimated_tokens=est_i or est_o)


_REAL_CLASSES = {"claude": AnthropicProvider, "openai": OpenAIProvider, "gemini": GeminiProvider}


class UnknownProviderError(KeyError):
    """カタログに存在しないプロバイダ名."""


def build_provider(name: str) -> BaseProvider:
    """1プロバイダを構築. キーが無ければ MockProvider を返す."""
    registry = _registry()
    if name not in registry:
        raise UnknownProviderError(
            f"unknown provider {name!r}; known: {', '.join(sorted(registry))}")
    entry = registry[name]
    spec = entry["spec"]
    if not os.getenv(entry["key_env"]):
        return MockProvider(spec)              # フォールバック(キー無し)
    cls = _REAL_CLASSES.get(name)
    if cls is None:
        # カタログに追加されたが実装クラスが無い場合。黙って mock にすると
        # 「実 API を使っているつもりで mock」という最悪の誤認を生むため警告する。
        logger.warning("no real implementation for provider %r; using mock", name)
        return MockProvider(spec)
    return cls(spec)                           # 実API


def build_providers(names: list[str] | None = None) -> dict[str, BaseProvider]:
    registry = _registry()
    names = names if names is not None else sorted(registry)
    known = [n for n in names if n in registry]
    for n in names:
        if n not in registry:
            logger.warning("unknown provider %r ignored", n)
    if not known:
        raise ValueError(
            f"no known providers among {names!r}; known: {', '.join(sorted(registry))}")
    return {n: build_provider(n) for n in known}


def provider_mode(name: str) -> str:
    """"real"(キーあり) / "mock"(キー無し) / "unknown" を返す.

    旧実装は未知の名前で KeyError を投げ、/v1/providers が 500 になっていた。
    """
    registry = _registry()
    entry = registry.get(name)
    if entry is None:
        return "unknown"
    return "real" if os.getenv(entry["key_env"]) else "mock"
