"""Jev に「このリクエストは安いモデルで足りるか、強いモデルが要るか」を判断させる助言役.

元の考え方: Bober_smart「Full guide to Jev 10 steps for setup」Step 08（ハーネスにモデルルーターを置く）。
判断は Jev、決まりはコード、実行はルーター（LLMRouter）に任せる。

- 既定は off。AI_PLATFORM_JEV_ROUTING=shadow で「助言を記録するだけ」、on で採用する
- 呼び出し側が strategy を明示した（balanced 以外）なら、その指定を優先して Jev には聞かない
- 秘密らしい文字列・個人情報（メールアドレス・電話番号・カード番号らしき数字）を含む、または長すぎる
  プロンプトは外部 API に送らない
- 採用するのは「1位の確率 0.6 以上、かつ 2位との差 0.2 以上」のときだけ。それ以外・失敗時は要求どおり
- ログと記録にプロンプト本文は残さない（文字数だけ）
- 予算ガード（effective_strategy）はこの後に効くので、予算超過時の cost への降格は変わらない
"""
from __future__ import annotations

import logging
import re
import threading
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from .jev_client import MODEL, JevError
from .llm_router import RoutingStrategy

logger = logging.getLogger("ai_platform.jev_advisor")


class AdviceMode(str, Enum):
    OFF = "off"
    SHADOW = "shadow"   # 助言を記録するだけ（使う戦略は変えない）
    ON = "on"           # はっきりした助言なら採用する


def parse_mode(raw: str | None) -> AdviceMode:
    """環境変数の値を読む。不明な値は安全側（off）にする."""
    try:
        return AdviceMode((raw or "").strip().lower() or "off")
    except ValueError:
        logger.warning("unknown AI_PLATFORM_JEV_ROUTING value; using 'off'")
        return AdviceMode.OFF


# 選択肢には1行の説明を付ける
# （説明があると正解率もカバレッジも上がった: Gota「jevを業務に入れるまでにやること」）
TIERS = {
    "cost": "Short routine work a small model does well: classify, extract, reformat, short translation, "
            "rephrase, or a question with a short factual answer.",
    "balanced": "Ordinary questions or explanations that are neither clearly routine nor clearly demanding.",
    "quality": "Demanding work: multi-step reasoning, writing or fixing code, structuring a long document, "
               "or expert judgment where mistakes are costly.",
}
TIER_TO_STRATEGY = {"cost": RoutingStrategy.COST, "balanced": RoutingStrategy.BALANCED,
                    "quality": RoutingStrategy.QUALITY}

MIN_TOP = 0.6
MIN_MARGIN = 0.2
MAX_PROMPT_CHARS = 4000

SENSITIVE = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.-]+"                              # メールアドレス
    r"|(?<!\d)0\d{1,4}-\d{1,4}-\d{3,4}(?!\d)"                # 日本の電話番号
    r"|(?<!\d)(?:\d[ -]?){13,19}(?!\d)"                      # カード番号らしき数字の並び
    r"|AKIA[0-9A-Z]{16}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|(api[_-]?key|secret|token|password|passwd)\s*[:=]\s*[\"']?[^\s\"']{8,}",
    re.IGNORECASE,
)


@dataclass
class Advice:
    requested: RoutingStrategy
    used: RoutingStrategy
    mode: AdviceMode
    reason: str
    suggested: RoutingStrategy | None = None
    adopted: bool = False
    probabilities: dict[str, float] = field(default_factory=dict)
    prompt_chars: int = 0

    def as_log(self) -> dict:
        """記録用。プロンプト本文は含めない."""
        return {"mode": self.mode.value, "requested": self.requested.value, "used": self.used.value,
                "suggested": self.suggested.value if self.suggested else None, "adopted": self.adopted,
                "probabilities": self.probabilities, "prompt_chars": self.prompt_chars, "reason": self.reason}


class StrategyAdvisor:
    def __init__(self, post: Callable[[dict], dict], mode: AdviceMode = AdviceMode.OFF,
                 min_top: float = MIN_TOP, min_margin: float = MIN_MARGIN,
                 max_prompt_chars: int = MAX_PROMPT_CHARS) -> None:
        self.post = post
        self.mode = mode
        self.min_top = min_top
        self.min_margin = min_margin
        self.max_prompt_chars = max_prompt_chars
        self._stats: Counter[str] = Counter()
        self._lock = threading.Lock()

    def _count(self, key: str) -> None:
        with self._lock:
            self._stats[key] += 1

    def stats(self) -> dict:
        with self._lock:
            return dict(self._stats)

    def _keep(self, requested: RoutingStrategy, reason: str, chars: int, **kw) -> Advice:
        return Advice(requested=requested, used=requested, mode=self.mode, reason=reason,
                      prompt_chars=chars, **kw)

    def advise(self, prompt: str, requested: RoutingStrategy) -> Advice:
        chars = len(prompt or "")
        if self.mode == AdviceMode.OFF:
            return self._keep(requested, "Jev の助言は無効（off）", chars)
        if requested != RoutingStrategy.BALANCED:
            self._count("skipped")
            reason = f"呼び出し側が {requested.value} を指定したので聞かない"
            return self._log(self._keep(requested, reason, chars))
        if SENSITIVE.search(prompt or ""):
            self._count("skipped")
            reason = "秘密・個人情報らしい内容があるため外部 API に送らない"
            return self._log(self._keep(requested, reason, chars))
        if chars > self.max_prompt_chars:
            self._count("skipped")
            return self._log(self._keep(requested, f"プロンプトが {chars} 文字で長すぎるため送らない", chars))

        payload = {"model": MODEL, "state": {"prompt": prompt},
                   "questions": {"tier": {
                       "type": "choice",
                       "instructions": "Which model tier does `prompt` need to be answered well?",
                       "criteria": TIERS}}}
        self._count("asked")
        try:
            resp = self.post(payload)
            ans = (resp.get("answers") or {}).get("tier") if isinstance(resp, dict) else None
            probs = ans.get("probabilities") if isinstance(ans, dict) else None
            if not isinstance(probs, dict) or set(probs) - set(TIERS) or not probs:
                raise JevError("答えが choice の形ではない")
            probs = {k: float(v) for k, v in probs.items()}
            top = max(probs, key=probs.get)
        except Exception as e:  # noqa: BLE001 — どんな失敗でも要求どおりに戻す
            self._count("failed")
            reason = f"Jev の呼び出しに失敗したため要求どおり（{type(e).__name__}）"
            return self._log(self._keep(requested, reason, chars))

        ranked = sorted(probs.values(), reverse=True)
        first, second = ranked[0], (ranked[1] if len(ranked) > 1 else 0.0)
        suggested = TIER_TO_STRATEGY[top]
        if first < self.min_top:
            return self._log(self._keep(requested, f"1位の確率 {first:.2f} が {self.min_top} 未満", chars,
                                        suggested=suggested, probabilities=probs))
        if first - second < self.min_margin - 1e-9:
            reason = f"1位と2位の差 {first - second:.2f} が {self.min_margin} 未満"
            return self._log(self._keep(requested, reason, chars, suggested=suggested, probabilities=probs))
        if self.mode == AdviceMode.SHADOW:
            self._count("shadow_clear")
            return self._log(self._keep(requested, "shadow: 助言を記録だけして使わない", chars,
                                        suggested=suggested, probabilities=probs))
        self._count("adopted")
        return self._log(Advice(requested=requested, used=suggested, mode=self.mode,
                                reason="Jev の助言を採用",
                                suggested=suggested, adopted=True, probabilities=probs, prompt_chars=chars))

    def _log(self, advice: Advice) -> Advice:
        logger.info("jev_routing_advice", extra={"jev_advice": advice.as_log()})
        return advice
