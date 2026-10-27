"""最小の API キー認証. 実運用では KMS/DB 管理に差し替える前提.

セキュリティ上の限界(正直表記):
  - 保存は SHA-256。API キーが十分な高エントロピー(>=128bit のランダム)である前提。
    人間が決めた短いキーだと総当たり/レインボーテーブルに耐えられない。
    低エントロピーのキーを使うなら bcrypt/scrypt 等へ差し替えること。
  - キーの失効・ローテーション・スコープは未実装。
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets

logger = logging.getLogger("ai_platform.auth")

MIN_RECOMMENDED_KEY_LEN = 24


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def generate_api_key(nbytes: int = 32) -> str:
    """高エントロピーな API キーを生成する(運用時はこれを使う)."""
    return secrets.token_urlsafe(nbytes)


class APIKeyStore:
    """key(平文) -> tenant_id を保持。比較はハッシュで行う."""

    def __init__(self, keys: dict[str, str] | None = None) -> None:
        # {hashed_key: tenant_id}
        self._keys: dict[str, str] = {}
        for raw, tenant in (keys or {}).items():
            self.add(raw, tenant)

    @classmethod
    def from_env(cls, var: str = "AI_PLATFORM_API_KEYS") -> APIKeyStore:
        """環境変数 "key1:tenantA,key2:tenantB" 形式から読み込む.

        キー自体に ':' が含まれる場合を考慮し、**最後の** ':' で分割する
        (secrets.token_urlsafe は ':' を含まないが、外部発行キーは含みうる)。
        """
        raw = os.getenv(var, "").strip()
        keys: dict[str, str] = {}
        if raw:
            for pair in raw.split(","):
                pair = pair.strip()
                if not pair:
                    continue
                if ":" not in pair:
                    logger.warning("malformed entry in %s (expected 'key:tenant'); skipped", var)
                    continue
                k, t = pair.rsplit(":", 1)
                k, t = k.strip(), t.strip()
                if not k or not t:
                    logger.warning("empty key or tenant in %s; skipped", var)
                    continue
                keys[k] = t
        return cls(keys)

    def add(self, raw_key: str, tenant_id: str) -> None:
        if not raw_key or not tenant_id:
            raise ValueError("api key and tenant_id must be non-empty")
        if len(raw_key) < MIN_RECOMMENDED_KEY_LEN:
            # 弱いキーを黙って受け入れると本番に持ち込まれる。警告は残す。
            logger.warning(
                "api key for tenant %r is shorter than %d chars; use generate_api_key()",
                tenant_id, MIN_RECOMMENDED_KEY_LEN)
        self._keys[_hash(raw_key)] = tenant_id

    def resolve_tenant(self, raw_key: str | None) -> str | None:
        if not raw_key:
            return None
        digest = _hash(raw_key)
        # dict の直接参照ではなく定数時間比較で全件を走査する。
        # 辞書探索はハッシュ後の値に対する操作なので実害は小さいが、
        # 早期 return による分岐タイミング差を残さないため明示的に揃える。
        found: str | None = None
        for known_hash, tenant in self._keys.items():
            if hmac.compare_digest(known_hash, digest):
                found = tenant
        return found

    def verify(self, raw_key: str | None) -> bool:
        return self.resolve_tenant(raw_key) is not None

    def __len__(self) -> int:
        return len(self._keys)
