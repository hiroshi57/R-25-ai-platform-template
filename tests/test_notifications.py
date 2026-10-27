"""通知層(スケジュール通達を Teams / Chatwork へ同報)のテスト.

ネットワークには一切触れない。送信は injectable な sender をフェイクに差し替え、
送信先 URL / ヘッダ / ボディを検証する。
"""
from __future__ import annotations

import urllib.error

import pytest

from core.notifications import (
    ChatworkNotifier,
    MultiNotifier,
    NotificationResult,
    TeamsNotifier,
    build_default_notifier,
)


class RecordingSender:
    """送信呼び出しを記録し、任意の status/例外を返すフェイク sender."""

    def __init__(self, status: int = 200, raise_exc: Exception | None = None):
        self.calls = []
        self.status = status
        self.raise_exc = raise_exc

    def __call__(self, url, data, headers, timeout=None):
        self.calls.append({"url": url, "data": data, "headers": headers, "timeout": timeout})
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.status


# --- TeamsNotifier -------------------------------------------------------------

def test_teams_notifier_posts_json_text():
    sender = RecordingSender()
    n = TeamsNotifier("https://outlook.office.com/webhook/abc", sender=sender)
    res = n.send("デプロイ完了のお知らせ")

    assert res.ok is True and res.skipped is False and res.channel == "teams"
    assert len(sender.calls) == 1
    call = sender.calls[0]
    assert call["url"] == "https://outlook.office.com/webhook/abc"
    assert call["headers"]["Content-Type"] == "application/json"
    import json
    assert json.loads(call["data"].decode("utf-8")) == {"text": "デプロイ完了のお知らせ"}


# --- ChatworkNotifier ----------------------------------------------------------

def test_chatwork_notifier_posts_form_with_token_header():
    sender = RecordingSender()
    n = ChatworkNotifier("tok-123", "9999", sender=sender)
    res = n.send("会議は 15:00 からです")

    assert res.ok is True and res.channel == "chatwork"
    call = sender.calls[0]
    assert call["url"] == "https://api.chatwork.com/v2/rooms/9999/messages"
    assert call["headers"]["X-ChatWorkToken"] == "tok-123"
    assert call["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    # body=<url-encoded message> のフォームエンコードであること
    from urllib.parse import parse_qs
    parsed = parse_qs(call["data"].decode("utf-8"))
    assert parsed["body"] == ["会議は 15:00 からです"]


# --- 空メッセージ拒否 ----------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "   ", "\n\t"])
def test_notifier_rejects_empty_message(bad):
    n = TeamsNotifier("https://example.com/wh", sender=RecordingSender())
    with pytest.raises(ValueError):
        n.send(bad)


# --- 送信エラーは例外にせず失敗結果へ ------------------------------------------

def test_sender_error_becomes_failed_result_not_exception():
    sender = RecordingSender(raise_exc=urllib.error.URLError("connection refused"))
    n = TeamsNotifier("https://example.com/wh", sender=sender)
    res = n.send("x")
    assert res.ok is False and res.skipped is False
    assert res.error and "connection refused" in res.error


def test_http_error_records_status_code():
    err = urllib.error.HTTPError("https://example.com/wh", 429, "Too Many Requests", {}, None)
    n = ChatworkNotifier("t", "1", sender=RecordingSender(raise_exc=err))
    res = n.send("x")
    assert res.ok is False and res.status_code == 429


# --- MultiNotifier(同報 / 部分失敗の隔離) -------------------------------------

def test_multi_notifier_fans_out_to_all_channels():
    ts, cs = RecordingSender(), RecordingSender()
    multi = MultiNotifier([
        TeamsNotifier("https://example.com/wh", sender=ts),
        ChatworkNotifier("t", "1", sender=cs),
    ])
    results = multi.send("全員へ通達")
    assert len(results) == 2
    assert all(isinstance(r, NotificationResult) and r.ok for r in results)
    assert len(ts.calls) == 1 and len(cs.calls) == 1


def test_multi_notifier_isolates_one_channel_failure():
    failing = RecordingSender(raise_exc=urllib.error.URLError("down"))
    ok_sender = RecordingSender()
    multi = MultiNotifier([
        TeamsNotifier("https://example.com/wh", sender=failing),
        ChatworkNotifier("t", "1", sender=ok_sender),
    ])
    results = multi.send("通達")
    by_channel = {r.channel: r for r in results}
    assert by_channel["teams"].ok is False
    assert by_channel["chatwork"].ok is True     # 片方の障害で止まらない
    assert len(ok_sender.calls) == 1


def test_multi_notifier_all_ok_helper():
    multi = MultiNotifier([TeamsNotifier("https://example.com/wh", sender=RecordingSender())])
    results = multi.send("x")
    assert MultiNotifier.all_delivered(results) is True


# --- build_default_notifier(env からチャネル構築) -----------------------------

def test_build_default_notifier_skips_unconfigured(monkeypatch):
    for k in ("TEAMS_WEBHOOK_URL", "CHATWORK_API_TOKEN", "CHATWORK_ROOM_ID"):
        monkeypatch.delenv(k, raising=False)
    multi = build_default_notifier()
    assert multi.channels == []          # 未設定なら no-op(チャネルゼロ)


def test_build_default_notifier_includes_configured_channels(monkeypatch):
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "https://example.com/wh")
    monkeypatch.setenv("CHATWORK_API_TOKEN", "tok")
    monkeypatch.setenv("CHATWORK_ROOM_ID", "42")
    multi = build_default_notifier()
    assert set(multi.channels) == {"teams", "chatwork"}


def test_build_default_notifier_chatwork_needs_both_token_and_room(monkeypatch):
    monkeypatch.delenv("TEAMS_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("CHATWORK_API_TOKEN", "tok")
    monkeypatch.delenv("CHATWORK_ROOM_ID", raising=False)   # room 未設定
    multi = build_default_notifier()
    assert multi.channels == []          # token だけでは chatwork を構築しない
