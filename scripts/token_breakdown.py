"""harness-logs の worker-report から、ツール使用の内訳を月ごとの傾向として出す.

（提案 D / hiroshi57/harness-rules の .claude/rules/harness-retro.md 提案18）

使い方:
    python scripts/token_breakdown.py                       # 直近10件・テキスト出力
    python scripts/token_breakdown.py --limit 20 --json     # 直近20件・JSON 出力
    python scripts/token_breakdown.py --logs-dir <path>     # ログの場所を指定

注意:
- 候補の隣に tool-usage.measured[.<n>].json があればそれを使う
  （scripts/harness_log.py がトランスクリプトから実測したもの）。
  無いときだけ worker-report の tool_usage（Worker の自己申告）を使う
- 自己申告は判定の根拠にしない（CLAUDE.md §7）。
  出力の data_source / measured_records で、どちらを使ったかを確かめる
- 数えているのはトークン数ではなく、ツールの呼び出し回数
- 閾値による通知は出さない。テストの実行もコマンドに数えるため、健全なタスクでも比率は高くなる。
  比べるのは同じ数え方での月ごとの変化だけ
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT_LOGS_DIR = Path(".claude/harness-logs")
CALL_KEYS = ("read_calls", "search_calls", "bash_calls", "edit_calls", "other_calls")
MEASURED_STEM = "tool-usage.measured"   # scripts/harness_log.py がトランスクリプトから実測して置くファイル
RETRIEVAL_KEYS = ("read_calls", "search_calls", "bash_calls")
REPORT_NAME = re.compile(r"^worker-report(?:\.(\d+))?\.json$")   # 候補が複数あれば worker-report.<n>.json
REREAD_MIN_TASKS = 2


def _natural(s: str) -> tuple:
    """'43.10.1' が '43.9.1' より後に来るように並べる."""
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in re.split(r"[.\-_]", s))


def _clean_usage(usage: object) -> tuple[dict | None, list[str]]:
    """tool_usage の形を確かめる。(使える値, 捨てた項目の理由) を返す."""
    if usage is None:
        return None, []
    if not isinstance(usage, dict):
        return None, ["tool_usage がオブジェクトではない"]
    clean: dict = {}
    problems = []
    for k in (*CALL_KEYS, "largest_output_lines"):
        v = usage.get(k)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            problems.append(f"{k} が0以上の整数ではない: {v!r}")
            continue
        clean[k] = v
    rr = usage.get("reread_files")
    if rr is not None:
        if isinstance(rr, list) and all(isinstance(x, str) for x in rr):
            clean["reread_files"] = rr
        else:
            problems.append(f"reread_files が文字列の配列ではない: {type(rr).__name__}")
    return clean, problems


def collect_reports(logs_dir: Path, limit: int = 10) -> dict:
    """<logs_dir>/<project>/<YYYYMM>/<task_id>/worker-report[.<n>].json を新しい順に最大 limit 件読む."""
    found = []
    if logs_dir.is_dir():
        for f in logs_dir.glob("*/*/*/worker-report*.json"):
            m = REPORT_NAME.match(f.name)
            if m:
                found.append((f.parent.parent.name, _natural(f.parent.name), int(m.group(1) or 0), f))
        # フック（scripts/harness_log.py hook）が作る session-* / agent-* フォルダには worker-report が無い。
        # worker-report の無いフォルダの実測ファイルも、それぞれ1件として数える
        report_dirs = {f.parent for *_, f in found}
        for f in logs_dir.glob(f"*/*/*/{MEASURED_STEM}*.json"):
            m = re.match(rf"^{re.escape(MEASURED_STEM)}(?:\.(\d+))?\.json$", f.name)
            if m and f.parent not in report_dirs:
                found.append((f.parent.parent.name, _natural(f.parent.name), int(m.group(1) or 0), f))
    found.sort(key=lambda x: x[:3], reverse=True)

    records, skipped = [], []
    for month, _, cand, f in found[:limit]:
        if f.name.startswith(MEASURED_STEM):
            try:
                usage, problems = _clean_usage(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError) as e:
                skipped.append({"file": f.as_posix(), "reason": type(e).__name__})
                continue
            records.append({"project": f.parent.parent.parent.name, "month": month, "task_id": f.parent.name,
                            "source": "measured" if usage else None,
                            "candidate": cand, "tool_usage": usage, "problems": problems})
            continue
        try:
            report = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            skipped.append({"file": f.as_posix(), "reason": type(e).__name__})
            continue
        measured_file = f.parent / f"{MEASURED_STEM}{'.' + str(cand) if cand else ''}.json"
        source = "self_report"
        raw_usage = report.get("tool_usage") if isinstance(report, dict) else None
        if measured_file.is_file():
            try:
                raw_usage = json.loads(measured_file.read_text(encoding="utf-8"))
                source = "measured"
            except (OSError, json.JSONDecodeError):
                skipped.append({"file": measured_file.as_posix(), "reason": "JSONDecodeError"})
        usage, problems = _clean_usage(raw_usage)
        records.append({"project": f.parent.parent.parent.name, "month": month, "task_id": f.parent.name,
                        "source": source if usage else None,
                        "candidate": cand, "tool_usage": usage, "problems": problems})
    return {"records": records, "skipped": skipped}


def _overall_source(records: list[dict]) -> str | None:
    kinds = {r.get("source") for r in records} - {None}
    if not kinds:
        return None
    return kinds.pop() if len(kinds) == 1 else "mixed"


def summarize(collected: dict) -> dict:
    records = collected["records"]
    totals = dict.fromkeys(CALL_KEYS, 0)
    by_month: dict[str, dict] = defaultdict(lambda: dict.fromkeys(CALL_KEYS, 0))
    reread: Counter[str] = Counter()
    problems = []
    without = 0
    for r in records:
        if r["problems"]:
            problems.append({"task_id": r["task_id"], "candidate": r["candidate"], "problems": r["problems"]})
        u = r["tool_usage"]
        if not u:
            without += 1
            continue
        for k in CALL_KEYS:
            totals[k] += u.get(k, 0)
            by_month[r["month"]][k] += u.get(k, 0)
        reread.update(set(u.get("reread_files", [])))

    def share(t: dict) -> float | None:
        s = sum(t.values())
        return round(sum(t[k] for k in RETRIEVAL_KEYS) / s, 3) if s else None

    frequent = [{"file": p, "tasks": n} for p, n in sorted(reread.items(), key=lambda kv: (-kv[1], kv[0]))
                if n >= REREAD_MIN_TASKS]
    return {
        # measured = トランスクリプトからの実測（外部の証跡）
        # self_report = Worker の自己申告（判定の根拠にしない）
        "data_source": _overall_source(records),
        "measured_records": sum(1 for r in records if r.get("source") == "measured"),
        "self_report_records": sum(1 for r in records if r.get("source") == "self_report"),
        "records": len(records),
        "records_without_tool_usage": without,
        "skipped_files": collected["skipped"],
        "invalid_fields": problems,
        "totals": totals,
        "retrieval_share": share(totals),
        "trend_by_month": [{"month": m, "retrieval_share": share(t), **t}
                           for m, t in sorted(by_month.items())],
        "frequent_rereads": frequent,
    }


def _format_text(s: dict) -> str:
    lines = [
        f"※ 呼び出し回数。実測 {s['measured_records']} 件・自己申告 {s['self_report_records']} 件"
        "（自己申告は判定の根拠にしない）",
        f"対象: {s['records']} 件（tool_usage なし {s['records_without_tool_usage']} 件・"
        f"読めなかったファイル {len(s['skipped_files'])} 件・"
        f"形の違う項目あり {len(s['invalid_fields'])} 件）",
    ]
    if s["retrieval_share"] is None:
        lines.append("集計できる tool_usage がありません")
    else:
        t = s["totals"]
        lines.append(f"合計: 読み込み {t['read_calls']} / 検索 {t['search_calls']} / "
                     f"コマンド {t['bash_calls']} / 編集 {t['edit_calls']} / その他 {t['other_calls']}")
        lines.append("月ごとの「読み込み+検索+コマンド」の割合:")
        lines.extend(f"  {m['month']}: {m['retrieval_share']:.0%}" for m in s["trend_by_month"]
                     if m["retrieval_share"] is not None)
    for x in s["frequent_rereads"]:
        lines.append(f"複数タスクで読み直し: {x['file']}（{x['tasks']} タスク）")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--logs-dir", type=Path, default=DEFAULT_LOGS_DIR)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    s = summarize(collect_reports(a.logs_dir, a.limit))
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(s, ensure_ascii=False, indent=2) if a.json else _format_text(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
