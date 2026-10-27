"""通知層: 任意テキスト(スケジュール通達など)を Teams / Chatwork へ同報する.

設計方針(このリポジトリの規約に合わせる):
  - **標準ライブラリのみ**(urllib)で実装。core は third-party に依存させない。
  - **キー/URL 未設定のチャネルは no-op**。clone 直後(未設定)でも動く。
    プロバイダの「キー無し=mock フォールバック」と同じ思想。
  - **送信は injectable**。`sender` を差し替えればネットワーク無しでテストできる。
  - **部分失敗を隔離**する。片方のチャネル障害でもう片方の配信を止めない
    (各チャネルの送信を個別に try/except で囲む)。

外部連携仕様:
  - Teams: Incoming Webhook。`application/json` で `{"text": <message>}` を POST。
  - Chatwork: REST API。`POST https://api.chatwork.com/v2/rooms/{room_id}/messages`、
    ヘッダ `X-ChatWorkToken`、`application/x-www-form-urlencoded` で `body=<message>`。
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlencode, urlparse

logger = logging.getLogger("ai_platform.notifications")

# 1 送信あたりの上限秒数。タイムアウト無しだとハングした先で張り付く。
DEFAULT_TIMEOUT_SEC = float(os.getenv("AI_PLATFORM_NOTIFY_TIMEOUT_SEC", "10"))

CHATWORK_API_BASE = "https://api.chatwork.com/v2"

# sender(url, data, headers, timeout) -> status_code。非 2xx / 通信失敗は例外を送出する。
Sender = Callable[..., int]


@dataclass
class NotificationResult:
    """1 チャネル分の配信結果. 例外は投げず、常にこの結果へ畳み込む."""
    channel: str
    ok: bool
    skipped: bool = False
    status_code: int | None = None
    error: str | None = None

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _http_post(url: str, data: bytes, headers: dict, timeout: float = DEFAULT_TIMEOUT_SEC) -> int:
    """標準ライブラリだけで POST する既定 sender.

    URL は信頼できる env 由来だが、file:// 等の想定外スキームを弾いて
    ローカルファイル読み出し等の事故を防ぐ(http/https のみ許可)。
    """
    scheme = urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"unsupported url scheme {scheme!r}; only http/https allowed")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")  # noqa: S310 - スキーム検証済み
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - スキーム検証済み
        return int(getattr(resp, "status", 0) or resp.getcode())


class Notifier(ABC):
    """1 チャネルの通知先. 送信結果は例外ではなく NotificationResult で返す."""

    channel: str = "base"

    def __init__(self, sender: Sender | None = None):
        self._sender = sender or _http_post

    @abstractmethod
    def _build_request(self, message: str) -> tuple:
        """(url, data: bytes, headers: dict) を返す."""

    def send(self, message: str) -> NotificationResult:
        if message is None or not message.strip():
            raise ValueError("message must be a non-empty string")
        url, data, headers = self._build_request(message)
        try:
            status = self._sender(url, data, headers, timeout=DEFAULT_TIMEOUT_SEC)
        except urllib.error.HTTPError as exc:
            # 4xx/5xx はステータスを取得できる
            logger.warning("%s delivery failed: HTTP %s", self.channel, exc.code)
            return NotificationResult(self.channel, ok=False, status_code=exc.code, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - 通信失敗を配信結果へ畳み込む
            logger.warning("%s delivery failed: %s", self.channel, exc)
            return NotificationResult(self.channel, ok=False, error=str(exc))
        ok = 200 <= status < 300
        if not ok:
            logger.warning("%s delivery returned status %s", self.channel, status)
        return NotificationResult(self.channel, ok=ok, status_code=status)


class TeamsNotifier(Notifier):
    """Microsoft Teams の Incoming Webhook へ POST する."""

    channel = "teams"

    def __init__(self, webhook_url: str, sender: Sender | None = None):
        super().__init__(sender)
        self.webhook_url = webhook_url

    def _build_request(self, message: str) -> tuple:
        data = json.dumps({"text": message}, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        return self.webhook_url, data, headers


class ChatworkNotifier(Notifier):
    """Chatwork REST API へメッセージを投稿する."""

    channel = "chatwork"

    def __init__(self, api_token: str, room_id: str, sender: Sender | None = None):
        super().__init__(sender)
        self.api_token = api_token
        self.room_id = str(room_id)

    def _build_request(self, message: str) -> tuple:
        url = f"{CHATWORK_API_BASE}/rooms/{self.room_id}/messages"
        data = urlencode({"body": message}).encode("utf-8")
        headers = {
            "X-ChatWorkToken": self.api_token,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        return url, data, headers


class MultiNotifier:
    """複数チャネルへ同報する. 各チャネルの送信を隔離し、部分失敗を許容する."""

    def __init__(self, notifiers: list[Notifier]):
        self._notifiers = list(notifiers)

    @property
    def channels(self) -> list[str]:
        return [n.channel for n in self._notifiers]

    def send(self, message: str) -> list[NotificationResult]:
        results: list[NotificationResult] = []
        for n in self._notifiers:
            try:
                results.append(n.send(message))
            except ValueError:
                # 空メッセージ等の入力エラーは全チャネル共通なので上位へ伝播させる
                raise
            except Exception as exc:  # noqa: BLE001 - 想定外でも他チャネルを止めない
                logger.exception("unexpected error in %s notifier", n.channel)
                results.append(NotificationResult(n.channel, ok=False, error=str(exc)))
        return results

    @staticmethod
    def all_delivered(results: list[NotificationResult]) -> bool:
        """設定済みチャネルが 1 つ以上あり、その全てが配信成功したか."""
        actionable = [r for r in results if not r.skipped]
        return bool(actionable) and all(r.ok for r in actionable)


def build_default_notifier(sender: Sender | None = None) -> MultiNotifier:
    """env から設定済みチャネルだけを組み立てる(未設定チャネルは含めない).

    参照する環境変数:
      - TEAMS_WEBHOOK_URL       … Teams Incoming Webhook URL
      - CHATWORK_API_TOKEN      … Chatwork API トークン
      - CHATWORK_ROOM_ID        … 投稿先ルーム ID(token と両方揃って初めて有効)
    """
    notifiers: list[Notifier] = []

    teams_url = (os.getenv("TEAMS_WEBHOOK_URL") or "").strip()
    if teams_url:
        notifiers.append(TeamsNotifier(teams_url, sender=sender))

    cw_token = (os.getenv("CHATWORK_API_TOKEN") or "").strip()
    cw_room = (os.getenv("CHATWORK_ROOM_ID") or "").strip()
    if cw_token and cw_room:
        notifiers.append(ChatworkNotifier(cw_token, cw_room, sender=sender))
    elif cw_token or cw_room:
        # 片方だけの設定は事故(片肺運転)。黙って無視せず警告する。
        logger.warning(
            "chatwork notifier disabled: both CHATWORK_API_TOKEN and CHATWORK_ROOM_ID are required")

    return MultiNotifier(notifiers)
