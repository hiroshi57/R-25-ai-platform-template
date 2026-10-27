"""API キー無しで動く CLI デモ. `python -m app_template.demo` で実行."""
from __future__ import annotations

import json

from core import (
    LLMRouter,
    RoutingStrategy,
    Settings,
    build_providers,
    configure_logging,
    new_request_id,
    provider_mode,
)


def main() -> None:
    settings = Settings.from_env()
    configure_logging(level=settings.log_level, json_format=settings.log_json)
    new_request_id()

    # キーがあれば実API、無ければ mock に自動フォールバック
    print("=== プロバイダ切替(real/mock) ===")
    for name in settings.enabled_providers:
        print(f"  {name:7} -> {provider_mode(name)}")

    router = LLMRouter(providers=build_providers(settings.enabled_providers))
    prompt = "社内規程のうち経費精算の締め日を教えて"

    print("\n=== 戦略別ルーティング(APIキー不要) ===")
    for strategy in RoutingStrategy:
        c = router.route(prompt, strategy=strategy)
        print(f"[{strategy.value:8}] -> {c.provider:7} model={c.model:16} "
              f"cost=${c.cost_usd:.6f} latency={c.latency_ms:.0f}ms")

    print("\n=== 観測性サマリ ===")
    print(json.dumps(router.metrics.summary(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
