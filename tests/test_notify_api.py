"""POST /v1/notify/scheduled(スケジュール通達エンドポイント)のテスト.

外部スケジューラ(Cloud Scheduler 等)が叩く想定。認証は既存 API キー方式を流用。
ネットワークには触れず、フェイク notifier を create_app に注入する。
"""
from __future__ import annotations

import pytest

from core.notifications import NotificationResult


class FakeNotifier:
    """MultiNotifier 互換のフェイク。受け取ったメッセージと返す結果を制御する."""

    def __init__(self, results):
        self._results = results
        self.channels = [r.channel for r in results]
        self.received = []

    def send(self, message: str):
        self.received.append(message)
        return self._results


def _client(notifier):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from service.api import create_app
    return TestClient(create_app(notifier=notifier))


def test_notify_scheduled_requires_auth():
    c = _client(FakeNotifier([]))
    assert c.post("/v1/notify/scheduled", json={"message": "x"}).status_code == 401


def test_notify_scheduled_delivers_to_all_channels(auth_headers):
    fake = FakeNotifier([
        NotificationResult(channel="teams", ok=True, skipped=False, status_code=200),
        NotificationResult(channel="chatwork", ok=True, skipped=False, status_code=200),
    ])
    c = _client(fake)
    r = c.post("/v1/notify/scheduled", json={"message": "定例のお知らせ"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["delivered"] is True
    assert body["configured_channels"] == 2
    assert {res["channel"] for res in body["results"]} == {"teams", "chatwork"}
    assert fake.received == ["定例のお知らせ"]      # メッセージがそのまま渡る


def test_notify_scheduled_partial_failure_returns_502(auth_headers):
    fake = FakeNotifier([
        NotificationResult(channel="teams", ok=False, skipped=False, error="boom"),
        NotificationResult(channel="chatwork", ok=True, skipped=False, status_code=200),
    ])
    c = _client(fake)
    r = c.post("/v1/notify/scheduled", json={"message": "x"}, headers=auth_headers)
    assert r.status_code == 502, r.text
    body = r.json()["detail"]
    assert body["delivered"] is False
    assert any(res["channel"] == "teams" and res["ok"] is False for res in body["results"])


def test_notify_scheduled_no_channels_configured_is_noop(auth_headers):
    fake = FakeNotifier([])       # 何も設定されていない状態
    c = _client(fake)
    r = c.post("/v1/notify/scheduled", json={"message": "x"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["delivered"] is False and body["configured_channels"] == 0


def test_notify_scheduled_rejects_empty_message(auth_headers):
    c = _client(FakeNotifier([]))
    r = c.post("/v1/notify/scheduled", json={"message": "   "}, headers=auth_headers)
    assert r.status_code == 422       # pydantic のバリデーション(空白のみ不可)
