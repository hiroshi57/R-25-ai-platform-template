"""認証とレート制限の検証."""

from core import APIKeyStore, RateLimiter


def test_api_key_resolves_tenant():
    store = APIKeyStore({"secret-1": "tenant-a"})
    assert store.verify("secret-1") is True
    assert store.resolve_tenant("secret-1") == "tenant-a"
    assert store.verify("wrong") is False
    assert store.resolve_tenant(None) is None


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("AI_PLATFORM_API_KEYS", "k1:tenant-x, k2:tenant-y")
    store = APIKeyStore.from_env()
    assert store.resolve_tenant("k1") == "tenant-x"
    assert store.resolve_tenant("k2") == "tenant-y"


def test_rate_limiter_blocks_after_capacity():
    clock = {"t": 0.0}
    rl = RateLimiter(capacity=3, refill_per_sec=1.0, now_fn=lambda: clock["t"])
    assert rl.allow("tenant") is True
    assert rl.allow("tenant") is True
    assert rl.allow("tenant") is True
    assert rl.allow("tenant") is False  # capacity 使い切り


def test_rate_limiter_refills_over_time():
    clock = {"t": 0.0}
    rl = RateLimiter(capacity=2, refill_per_sec=1.0, now_fn=lambda: clock["t"])
    assert rl.allow("t") is True
    assert rl.allow("t") is True
    assert rl.allow("t") is False
    clock["t"] = 2.0  # 2 秒経過 -> 2 トークン回復
    assert rl.allow("t") is True
    assert rl.allow("t") is True


def test_rate_limiter_isolates_tenants():
    clock = {"t": 0.0}
    rl = RateLimiter(capacity=1, refill_per_sec=1.0, now_fn=lambda: clock["t"])
    assert rl.allow("tenant-a") is True
    assert rl.allow("tenant-a") is False
    assert rl.allow("tenant-b") is True  # 別テナントは独立
