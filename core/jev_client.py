"""TypeSafe の System One API（Jev）を呼ぶ最小のクライアント（標準ライブラリだけ）.

POST {base_url}/v1/systemone、認証は `Authorization: Bearer <TYPESAFE_API_KEY>`（docs.typesafe.ai/api）。
API キーは環境変数から読み、例外メッセージやログに出さない。応答本文もエラー時には出さない。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://api.typesafe.ai"
MODEL = "jev-latest"
RETRYABLE = (429, 529)


class JevError(RuntimeError):
    """Jev の呼び出しに失敗した（キーなし・HTTP エラー・応答の形の不一致など）."""


def post_systemone(payload: dict, api_key: str | None = None, base_url: str | None = None,
                   timeout: float = 15.0, attempts: int = 3) -> dict:
    """payload を送り、応答の dict を返す。429 / 529 は 1, 2 秒待って送り直す."""
    key = api_key or os.environ.get("TYPESAFE_API_KEY")
    if not key:
        raise JevError("TYPESAFE_API_KEY が設定されていない")
    url = (base_url or os.environ.get("TYPESAFE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/") + "/v1/systemone"
    if not url.startswith("https://"):
        raise JevError("base_url は https にしてください")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    for attempt in range(attempts):
        req = urllib.request.Request(url, data=body, method="POST", headers={  # noqa: S310 — https のみ
            "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in RETRYABLE and attempt < attempts - 1:
                time.sleep(2 ** attempt)
                continue
            raise JevError(f"HTTP {e.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
            raise JevError(type(e).__name__) from None
        if not isinstance(data, dict):
            raise JevError("応答が JSON オブジェクトではない")
        return data
    raise JevError("送り直しの上限に達した")
