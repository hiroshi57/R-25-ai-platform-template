"""永続化層(SQLite, 標準ライブラリ). LLM呼び出しメトリクスの保存. テナント分離.

旧実装の問題点と対応:
  - created_at が無く「今月の消費額」が算出できなかった(FinOps の月次予測が
    全期間の合計を今月分として扱っていた) -> created_at を追加し月次集計を実装
  - tenant_id にインデックスが無く全件スキャン -> インデックス追加
  - p95 計算が core/observability.py と重複 -> core 側の percentile を共有
  - 1 INSERT ごとに commit -> WAL + 呼び出し側でのバッチを可能に
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone

from core.observability import empty_summary, percentile

SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    strategy TEXT NOT NULL,
    cost_usd REAL NOT NULL,
    latency_ms REAL NOT NULL,
    fell_back INTEGER NOT NULL,
    ok INTEGER NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metrics_tenant ON metrics(tenant_id);
CREATE INDEX IF NOT EXISTS idx_metrics_tenant_created ON metrics(tenant_id, created_at);
"""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ServiceDB:
    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        # check_same_thread=False で複数スレッドから使うため、書き込みは自前で直列化する。
        # sqlite3 の接続オブジェクトはスレッドセーフではない。
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        if path != ":memory:":
            # 同時読み書きでの "database is locked" を減らす
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """既存 DB に新しい列を足す(created_at 無しの旧DBからの移行)."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(metrics)")}
        for col, ddl in (
            ("input_tokens", "ALTER TABLE metrics ADD COLUMN input_tokens INTEGER NOT NULL DEFAULT 0"),
            ("output_tokens", "ALTER TABLE metrics ADD COLUMN output_tokens INTEGER NOT NULL DEFAULT 0"),
            ("created_at", "ALTER TABLE metrics ADD COLUMN created_at TEXT NOT NULL DEFAULT ''"),
        ):
            if col not in cols:
                self.conn.execute(ddl)

    def log_metric(self, tenant_id: str, provider: str, model: str, strategy: str,
                   cost_usd: float, latency_ms: float, fell_back: bool, ok: bool,
                   input_tokens: int = 0, output_tokens: int = 0,
                   created_at: str | None = None) -> None:
        if not tenant_id:
            raise ValueError("tenant_id must be non-empty")
        with self._lock:
            self.conn.execute(
                "INSERT INTO metrics(tenant_id, provider, model, strategy, cost_usd, latency_ms, "
                "fell_back, ok, input_tokens, output_tokens, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (tenant_id, provider, model, strategy, cost_usd, latency_ms,
                 1 if fell_back else 0, 1 if ok else 0, input_tokens, output_tokens,
                 created_at or _utcnow_iso()))
            self.conn.commit()

    def _summarize_rows(self, rows: list[sqlite3.Row]) -> dict:
        if not rows:
            return empty_summary()
        ok_rows = [r for r in rows if r["ok"]]
        # 失敗レコード(latency 0)を混ぜると p95 が実態より小さく出るため成功分のみ
        latencies = [r["latency_ms"] for r in ok_rows]
        by: dict[str, dict] = {}
        for r in ok_rows:
            b = by.setdefault(r["provider"],
                              {"count": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0})
            b["count"] += 1
            b["cost_usd"] = round(b["cost_usd"] + r["cost_usd"], 6)
            b["input_tokens"] += r["input_tokens"] or 0
            b["output_tokens"] += r["output_tokens"] or 0
        error_count = len(rows) - len(ok_rows)
        return {
            "count": len(rows),
            "ok_count": len(ok_rows),
            "error_count": error_count,
            "error_rate": round(error_count / len(rows), 4),
            "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 6),
            "p50_latency_ms": round(percentile(latencies, 50), 1),
            "p95_latency_ms": round(percentile(latencies, 95), 1),
            "p99_latency_ms": round(percentile(latencies, 99), 1),
            "fallback_rate": round(sum(1 for r in rows if r["fell_back"]) / len(rows), 4),
            "by_provider": by,
        }

    def summary(self, tenant_id: str) -> dict:
        rows = self.conn.execute(
            "SELECT provider, cost_usd, latency_ms, fell_back, ok, input_tokens, output_tokens "
            "FROM metrics WHERE tenant_id=?", (tenant_id,)).fetchall()
        return self._summarize_rows(rows)

    def month_to_date_cost(self, tenant_id: str, now: datetime | None = None) -> float:
        """当月分のコスト合計。FinOps の月次予測はこれを使う."""
        now = now or datetime.now(timezone.utc)
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        row = self.conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) AS total FROM metrics "
            "WHERE tenant_id=? AND created_at >= ?", (tenant_id, start)).fetchone()
        return round(row["total"], 6)

    def daily_costs(self, tenant_id: str, days: int = 30) -> list[float]:
        """直近 N 日の日次コスト(異常検知の入力)."""
        rows = self.conn.execute(
            "SELECT substr(created_at, 1, 10) AS d, SUM(cost_usd) AS c FROM metrics "
            "WHERE tenant_id=? GROUP BY d ORDER BY d DESC LIMIT ?",
            (tenant_id, days)).fetchall()
        return [round(r["c"], 6) for r in reversed(rows)]

    def tenants(self) -> list[str]:
        return [r["tenant_id"] for r in
                self.conn.execute("SELECT DISTINCT tenant_id FROM metrics ORDER BY tenant_id")]

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def __enter__(self) -> ServiceDB:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
