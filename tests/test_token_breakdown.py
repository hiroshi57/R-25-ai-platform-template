"""scripts/token_breakdown.py のテスト（提案 D: ツール使用の内訳を月ごとの傾向として出す）."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("token_breakdown", ROOT / "scripts" / "token_breakdown.py")
tb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tb)


def _write(logs: Path, month: str, task_id: str, tool_usage=None, name: str = "worker-report.json",
           raw: str | None = None) -> Path:
    d = logs / "proj" / month / task_id
    d.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": "worker-report.v1", "status": "completed"}
    if tool_usage is not None:
        report["tool_usage"] = tool_usage
    f = d / name
    f.write_text(raw if raw is not None else json.dumps(report), encoding="utf-8")
    return f


def _u(read=0, search=0, bash=0, edit=0, reread=None):
    u = {"read_calls": read, "search_calls": search, "bash_calls": bash, "edit_calls": edit}
    if reread is not None:
        u["reread_files"] = reread
    return u


def _run(logs: Path, limit: int = 10) -> dict:
    return tb.summarize(tb.collect_reports(logs, limit))


def test_totals_and_retrieval_share(tmp_path):
    _write(tmp_path, "202609", "1.1", _u(read=6, search=2, bash=2, edit=10))
    s = _run(tmp_path)
    assert s["totals"] == {"read_calls": 6, "search_calls": 2, "bash_calls": 2, "edit_calls": 10,
                         "other_calls": 0}
    assert s["retrieval_share"] == pytest.approx(0.5)
    assert s["data_source"] == "self_report"


def test_no_threshold_advisory_even_for_typical_tdd_task(tmp_path):
    # 読み込み8・検索4・コマンド6・編集5 は健全なタスクの典型。比率が高くても警告しない
    _write(tmp_path, "202609", "1.1", _u(read=8, search=4, bash=6, edit=5))
    s = _run(tmp_path)
    assert "advisories" not in s
    assert s["retrieval_share"] == pytest.approx(0.783, abs=1e-3)


def test_trend_is_reported_per_month_in_order(tmp_path):
    _write(tmp_path, "202608", "1.1", _u(read=1, edit=1))
    _write(tmp_path, "202609", "2.1", _u(read=3, edit=1))
    trend = _run(tmp_path)["trend_by_month"]
    assert [(m["month"], m["retrieval_share"]) for m in trend] == [("202608", 0.5), ("202609", 0.75)]


def test_numbered_candidate_reports_are_included(tmp_path):
    _write(tmp_path, "202609", "1.1", _u(read=1), name="worker-report.1.json")
    _write(tmp_path, "202609", "1.1", _u(read=2), name="worker-report.2.json")
    _write(tmp_path, "202609", "1.1", _u(read=9), name="worker-report.json.bak")   # 対象外
    s = _run(tmp_path)
    assert s["records"] == 2 and s["totals"]["read_calls"] == 3


def test_limit_keeps_newest_by_month_then_natural_task_order(tmp_path):
    _write(tmp_path, "202608", "old", _u(edit=100))
    _write(tmp_path, "202609", "43.9.1", _u(read=1))
    _write(tmp_path, "202609", "43.10.1", _u(read=1))
    collected = tb.collect_reports(tmp_path, limit=2)
    assert [r["task_id"] for r in collected["records"]] == ["43.10.1", "43.9.1"]


def test_wrong_types_are_dropped_and_reported(tmp_path):
    _write(tmp_path, "202609", "1.1", {"read_calls": True, "edit_calls": 1, "search_calls": -2,
                                       "reread_files": "src/a.py"})
    s = _run(tmp_path)
    assert s["totals"]["read_calls"] == 0 and s["totals"]["search_calls"] == 0 and s["totals"]["edit_calls"] == 1
    assert s["frequent_rereads"] == []                       # 文字列を1文字ずつ数えない
    msgs = s["invalid_fields"][0]["problems"]
    assert any("read_calls" in m for m in msgs) and any("reread_files" in m for m in msgs)


def test_files_reread_in_multiple_tasks_are_reported(tmp_path):
    _write(tmp_path, "202609", "1.1", _u(read=1, reread=["core/router.py", "a.py"]))
    _write(tmp_path, "202609", "1.2", _u(read=1, reread=["core/router.py"]))
    assert _run(tmp_path)["frequent_rereads"] == [{"file": "core/router.py", "tasks": 2}]


def test_missing_tool_usage_and_broken_json_are_counted_not_failed(tmp_path):
    _write(tmp_path, "202609", "1.1", None)
    _write(tmp_path, "202609", "1.2", raw="{not json")
    _write(tmp_path, "202609", "1.3", _u(read=1, edit=1))
    s = _run(tmp_path)
    assert s["records"] == 2 and s["records_without_tool_usage"] == 1 and len(s["skipped_files"]) == 1


def test_no_logs_returns_empty_summary(tmp_path):
    s = _run(tmp_path / "missing")
    assert s["records"] == 0 and s["retrieval_share"] is None and s["trend_by_month"] == []


def _measured(logs: Path, month: str, task_id: str, usage: dict, suffix: str = "") -> None:
    d = logs / "proj" / month / task_id
    d.mkdir(parents=True, exist_ok=True)
    (d / f"tool-usage.measured{suffix}.json").write_text(json.dumps({"data_source": "measured", **usage}),
                                                         encoding="utf-8")


def test_measured_usage_is_preferred_over_self_report(tmp_path):
    _write(tmp_path, "202610", "1.1", _u(read=99, edit=1))           # 自己申告は 99
    _measured(tmp_path, "202610", "1.1", _u(read=2, edit=1))         # 実測は 2
    s = _run(tmp_path)
    assert s["totals"]["read_calls"] == 2
    assert (s["data_source"], s["measured_records"], s["self_report_records"]) == ("measured", 1, 0)


def test_measured_file_is_matched_to_its_candidate_number(tmp_path):
    _write(tmp_path, "202610", "1.1", _u(read=50), name="worker-report.json")
    _write(tmp_path, "202610", "1.1", _u(read=50), name="worker-report.2.json")
    _measured(tmp_path, "202610", "1.1", _u(read=1))                 # 1件目の実測
    _measured(tmp_path, "202610", "1.1", _u(read=3), suffix=".2")    # 2件目の実測
    s = _run(tmp_path)
    assert s["totals"]["read_calls"] == 4 and s["measured_records"] == 2


def test_mixed_sources_are_reported_as_mixed(tmp_path):
    _write(tmp_path, "202610", "1.1", _u(read=1))
    _measured(tmp_path, "202610", "1.1", _u(read=1))
    _write(tmp_path, "202610", "1.2", _u(read=1))                    # こちらは自己申告のみ
    assert _run(tmp_path)["data_source"] == "mixed"


def test_other_calls_from_measurement_are_counted(tmp_path):
    _write(tmp_path, "202610", "1.1")
    _measured(tmp_path, "202610", "1.1", {**_u(read=1), "other_calls": 3})
    s = _run(tmp_path)
    assert s["totals"]["other_calls"] == 3 and s["retrieval_share"] == pytest.approx(0.25)


def test_measured_only_dirs_from_hook_are_counted(tmp_path):
    # フックが作る session-* / agent-* フォルダには worker-report が無い。実測だけでも1件として数える
    _measured(tmp_path, "202610", "session-abc", _u(read=4, edit=1))
    _measured(tmp_path, "202610", "agent-xyz", _u(search=2))
    _write(tmp_path, "202610", "1.1", _u(read=1))                    # 通常のタスク（自己申告）
    s = _run(tmp_path)
    assert s["records"] == 3 and s["measured_records"] == 2 and s["self_report_records"] == 1
    assert s["totals"]["read_calls"] == 5 and s["totals"]["search_calls"] == 2
