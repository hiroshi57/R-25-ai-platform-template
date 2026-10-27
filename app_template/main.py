"""新規 AI アプリの雛形. これを clone/コピーして機能を足すだけで、
認証・レート制限・LLM自動ルーティング・観測性・構造化ロギングが最初から揃う。

- 設定は Settings.from_env() で一元管理
- プロバイダはキーがあれば実API、無ければ mock に自動フォールバック
- 全リクエストに request_id を付与しログ相関

FastAPI が入っていれば `uvicorn app_template.main:app --reload` で起動。

注意: これは **雛形(単一プロセス・インメモリ)** であり、永続化・テナント別集計が
必要な場合は service/api.py を使うこと。
"""
from __future__ import annotations

import logging
import os

from core import (
    APIKeyStore,
    LLMRouter,
    NoProviderAvailable,
    RateLimiter,
    RoutingStrategy,
    Settings,
    build_providers,
    configure_logging,
    generate_api_key,
    get_request_id,
    new_request_id,
    provider_mode,
    set_request_id,
    summarize,
)

logger = logging.getLogger("ai_platform.app")

# --- 設定と共通基盤の初期化 ---
SETTINGS = Settings.from_env()
configure_logging(level=SETTINGS.log_level, json_format=SETTINGS.log_json)

API_KEYS = APIKeyStore.from_env()

# 旧実装は「demo-key」を無条件で有効キーとして登録していた。
# .env.example にも同じ値が書かれており、本番デプロイでも常に
# demo-key で認証を通過できるバックドアになっていた。
# 開発用の自動登録は AI_PLATFORM_DEV_MODE=1 のときだけに限定する。
DEV_MODE = os.getenv("AI_PLATFORM_DEV_MODE", "").strip().lower() in ("1", "true", "yes", "on")
if len(API_KEYS) == 0:
    if DEV_MODE:
        API_KEYS.add("demo-key-local-development-only", "demo-tenant")
        logger.warning(
            "DEV MODE: registered a well-known demo API key. Never enable in production.")
    else:
        # キーが 1 本も無い状態で起動させると、全リクエストが 401 になるだけで
        # 原因が分かりにくい。生成例を提示して設定を促す。
        logger.error(
            "no API keys configured. Set AI_PLATFORM_API_KEYS='<key>:<tenant>' "
            "(example key: %s) or set AI_PLATFORM_DEV_MODE=1 for local development.",
            generate_api_key())

# キーがあれば実API、無ければ mock で動く
ROUTER = LLMRouter(providers=build_providers(SETTINGS.enabled_providers))
RATE_LIMITER = RateLimiter(capacity=SETTINGS.rate_capacity,
                           refill_per_sec=SETTINGS.rate_refill_per_sec)

# テナント別にメトリクスを分離する。旧実装の /v1/metrics は
# ROUTER.metrics.summary() を返しており、**全テナントの合計コスト** が
# 認証済みの任意のテナントに見えていた(コスト情報の越境)。
_TENANT_METRICS: dict = {}

# 重要: Pydantic モデルはモジュールスコープに置く。
# `from __future__ import annotations` 下で create_app() 内にローカル定義すると、
# FastAPI が型注釈(文字列)をモジュール globals から解決できず、
# ボディではなくクエリパラメータ扱いになり /v1/chat が常に 422 を返す。
# 旧実装はまさにこの状態で、README のクイックスタートが動作しなかった。
try:
    from pydantic import BaseModel, Field

    class ChatRequest(BaseModel):
        prompt: str = Field(..., min_length=1, max_length=32_000)
        strategy: RoutingStrategy = SETTINGS.default_strategy
        max_output_tokens: int = Field(256, gt=0, le=8192)
except ImportError:  # pragma: no cover - pydantic 未インストール環境
    ChatRequest = None  # type: ignore[assignment]


def create_app():
    from fastapi import Depends, FastAPI, Header, HTTPException, Request
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI(title="AI Platform Template", version="1.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=SETTINGS.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-API-Key", "X-Request-Id"],
        expose_headers=["X-Request-Id"],
    )

    @app.middleware("http")
    async def request_id_mw(request: Request, call_next):
        set_request_id(request.headers.get("x-request-id") or new_request_id())
        rid = get_request_id()
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        return response

    def auth_and_limit(x_api_key: str = Header(default="", alias="X-API-Key")) -> str:
        tenant = API_KEYS.resolve_tenant(x_api_key)
        if tenant is None:
            raise HTTPException(status_code=401, detail="invalid api key")
        if not RATE_LIMITER.allow(tenant):
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        return tenant

    @app.post("/v1/chat")
    def chat(req: ChatRequest, tenant: str = Depends(auth_and_limit)):
        try:
            c, metric = ROUTER.route_with_metric(
                req.prompt, strategy=req.strategy, max_output_tokens=req.max_output_tokens)
        except NoProviderAvailable as exc:
            logger.error("all providers failed: %s", exc)
            raise HTTPException(status_code=503, detail="all upstream providers unavailable") from exc
        _TENANT_METRICS.setdefault(tenant, []).append(metric)
        return {
            "request_id": get_request_id(), "tenant": tenant, "text": c.text,
            "provider": c.provider, "model": c.model,
            "cost_usd": c.cost_usd, "latency_ms": c.latency_ms,
            "input_tokens": c.input_tokens, "output_tokens": c.output_tokens,
            "estimated_tokens": c.estimated_tokens,
        }

    @app.get("/v1/metrics")
    def metrics(tenant: str = Depends(auth_and_limit)):
        # 自テナント分のみ返す
        return summarize(_TENANT_METRICS.get(tenant, []))

    @app.get("/v1/providers")
    def providers():
        return {name: provider_mode(name) for name in SETTINGS.enabled_providers}

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "providers": sorted(ROUTER.providers)}

    return app


# uvicorn エントリポイント(起動失敗は握り潰さずログに残して再送出)
try:  # pragma: no cover
    app = create_app()
except Exception:
    logger.exception("failed to create FastAPI app")
    raise
