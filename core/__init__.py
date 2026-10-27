"""ai-platform-template core: 認証/レート制限/観測性/LLMルーティングの共通基盤."""
from .auth import APIKeyStore, generate_api_key
from .config import Settings
from .finops import (
    BudgetGuard,
    BudgetStatus,
    CostAnomaly,
    days_in_month_of,
    detect_cost_anomaly,
    project_month_end,
)
from .llm_router import DEFAULT_WEIGHTS, LLMRouter, NoProviderAvailable, RoutingStrategy
from .logging import configure_logging, get_request_id, new_request_id, set_request_id
from .notifications import (
    ChatworkNotifier,
    MultiNotifier,
    NotificationResult,
    Notifier,
    TeamsNotifier,
    build_default_notifier,
)
from .observability import (
    MetricsStore,
    RequestMetric,
    empty_summary,
    percentile,
    summarize,
)
from .pricing import PRICING_SOURCE, ModelEntry, load_catalog, unverified_providers
from .providers import (
    BaseProvider,
    Completion,
    FailingProvider,
    MockProvider,
    ProviderSpec,
    default_mock_providers,
    default_specs,
    estimate_tokens,
)
from .rate_limit import RateLimiter
from .real_providers import (
    PROVIDER_REGISTRY,
    UnknownProviderError,
    build_provider,
    build_providers,
    provider_mode,
)

__version__ = "1.1.0"

__all__ = [
    "__version__",
    "APIKeyStore",
    "generate_api_key",
    "LLMRouter",
    "RoutingStrategy",
    "NoProviderAvailable",
    "DEFAULT_WEIGHTS",
    "MetricsStore",
    "RequestMetric",
    "summarize",
    "empty_summary",
    "percentile",
    "ModelEntry",
    "load_catalog",
    "unverified_providers",
    "PRICING_SOURCE",
    "BaseProvider",
    "Completion",
    "FailingProvider",
    "MockProvider",
    "ProviderSpec",
    "default_mock_providers",
    "default_specs",
    "estimate_tokens",
    "RateLimiter",
    "Settings",
    "configure_logging",
    "new_request_id",
    "get_request_id",
    "set_request_id",
    "build_providers",
    "build_provider",
    "provider_mode",
    "PROVIDER_REGISTRY",
    "UnknownProviderError",
    "BudgetGuard",
    "BudgetStatus",
    "project_month_end",
    "detect_cost_anomaly",
    "CostAnomaly",
    "days_in_month_of",
    "Notifier",
    "TeamsNotifier",
    "ChatworkNotifier",
    "MultiNotifier",
    "NotificationResult",
    "build_default_notifier",
]
