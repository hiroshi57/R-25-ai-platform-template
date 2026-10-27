"""構造化ロギング + リクエストID相関.

新規アプリが clone しただけで、JSON 構造化ログ・リクエストID の相関が使える。
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import sys
import uuid

# リクエスト単位で伝播する相関ID
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def new_request_id() -> str:
    rid = uuid.uuid4().hex[:12]
    _request_id.set(rid)
    return rid


def set_request_id(rid: str) -> None:
    _request_id.set(rid)


def get_request_id() -> str:
    return _request_id.get()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": get_request_id(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # json.dumps はシリアライズ不能な値で例外を投げる。ログ出力で
        # アプリを落とさないよう default=str で保険をかける。
        return json.dumps(payload, ensure_ascii=False, default=str)


class RequestIdFilter(logging.Filter):
    """テキスト形式でも %(request_id)s を使えるようにする."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


def configure_logging(level: str = "INFO", json_format: bool = True) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
        # ハンドラを外すだけだとファイル/ソケットが開いたままリークする
        with contextlib.suppress(Exception):  # クローズ失敗でアプリを落とさない
            h.close()
    handler = logging.StreamHandler(sys.stdout)
    if json_format:
        handler.setFormatter(JsonFormatter())
    else:
        # 旧実装は "rid=%(message)s" となっており、request_id を出さずに
        # 本文を rid= のラベルで出力していた(ログ相関が機能しない)。
        handler.addFilter(RequestIdFilter())
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] rid=%(request_id)s %(message)s"))
    root.addHandler(handler)
