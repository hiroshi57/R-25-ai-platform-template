"""設定管理. 環境変数から一元的にロードする(新規アプリはこれを import するだけ)."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from .llm_router import RoutingStrategy
from .pricing import load_catalog

logger = logging.getLogger("ai_platform.config")

_VALID_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}


def _env_bool(key: str, default: bool) -> bool:
    v = os.getenv(key)
    if v is None:
        return default
    v = v.strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off", ""):
        return False
    logger.warning("invalid boolean for %s=%r; using default %s", key, v, default)
    return default


def _env_float(key: str, default: float, minimum: float | None = None) -> float:
    """不正値でクラッシュせず既定値にフォールバックする.

    旧実装は float(os.getenv(...)) を素で呼んでいたため、typo 一つで
    import 時に ValueError となり、FastAPI 側の `except Exception: app = None`
    に飲み込まれて「原因不明で起動しない」状態になっていた。
    """
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        val = float(raw)
    except ValueError:
        logger.warning("invalid float for %s=%r; using default %s", key, raw, default)
        return default
    if minimum is not None and val < minimum:
        logger.warning("%s=%s is below minimum %s; using minimum", key, val, minimum)
        return minimum
    return val


@dataclass
class Settings:
    # ルーティング既定戦略
    default_strategy: RoutingStrategy = RoutingStrategy.BALANCED
    # レート制限
    rate_capacity: float = 20.0
    rate_refill_per_sec: float = 5.0
    # ロギング
    log_level: str = "INFO"
    log_json: bool = True
    # プロバイダの有効/無効(キーが無ければ自動でmockにフォールバック)
    enabled_providers: list[str] = field(
        default_factory=lambda: sorted(load_catalog().keys()))
    # 永続化先(既定はファイル。":memory:" を明示した場合のみ揮発)
    db_path: str = "ai_platform.db"
    # CORS 許可オリジン(既定は開発用の Vite / 静的配信)
    cors_origins: list[str] = field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])
    # 月次予算(USD)。0 なら予算ガード無効
    monthly_budget_usd: float = 0.0

    @classmethod
    def from_env(cls) -> Settings:
        strat = os.getenv("AI_PLATFORM_STRATEGY", "balanced").strip().lower()
        try:
            strategy = RoutingStrategy(strat)
        except ValueError:
            logger.warning("unknown strategy %r; falling back to 'balanced'", strat)
            strategy = RoutingStrategy.BALANCED

        catalog = load_catalog()
        raw_providers = os.getenv("AI_PLATFORM_PROVIDERS", ",".join(sorted(catalog)))
        requested = [p.strip() for p in raw_providers.split(",") if p.strip()]
        # カタログに無いプロバイダ名は握りつぶさず警告のうえ除外する。
        # 旧実装では build_providers が黙って除外する一方 /v1/providers 側は
        # そのまま参照して KeyError -> 500 になっていた。
        known = [p for p in requested if p in catalog]
        unknown = [p for p in requested if p not in catalog]
        if unknown:
            logger.warning("unknown providers ignored: %s (known: %s)",
                           ", ".join(unknown), ", ".join(sorted(catalog)))
        if not known:
            logger.warning("no valid providers configured; using full catalog")
            known = sorted(catalog)

        level = os.getenv("AI_PLATFORM_LOG_LEVEL", "INFO").strip().upper()
        if level not in _VALID_LOG_LEVELS:
            logger.warning("invalid log level %r; using INFO", level)
            level = "INFO"

        origins = [o.strip() for o in os.getenv(
            "AI_PLATFORM_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        ).split(",") if o.strip()]

        return cls(
            default_strategy=strategy,
            rate_capacity=_env_float("AI_PLATFORM_RATE_CAPACITY", 20.0, minimum=1.0),
            rate_refill_per_sec=_env_float("AI_PLATFORM_RATE_REFILL", 5.0, minimum=0.0),
            log_level=level,
            log_json=_env_bool("AI_PLATFORM_LOG_JSON", True),
            enabled_providers=known,
            db_path=os.getenv("AI_PLATFORM_DB_PATH", "ai_platform.db").strip() or "ai_platform.db",
            cors_origins=origins,
            monthly_budget_usd=_env_float("AI_PLATFORM_MONTHLY_BUDGET_USD", 0.0, minimum=0.0),
        )
