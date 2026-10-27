"""scripts/harness_log.py のテスト（提案 D: Worker の出力を harness-logs に逐語で保存し、ツール使用を実測する）."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("harness_log", ROOT / "scripts" / "harness_log.py")
hl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hl)


def _file(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / "in" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _tool_use(name: str, **inp) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "x", "name": name, "input": inp}]}}


def _tool_result(text: str, sidechain: bool = False) -> dict:
    return {"type": "user", "isSidechain": sidechain,
            "message": {"content": [{"type": "tool_result", "tool_use_id": "x", "content": text}]}}


def _transcript(tmp_path: Path, entries: list[dict]) -> Path:
    return _file(tmp_path, "agent.jsonl", "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n")


# --- 保存 --------------------------------------------------------------------

def test_save_copies_files_verbatim_into_task_dir(tmp_path):
    logs = tmp_path / "logs"
    report = _file(tmp_path, "wr.json", '{"status": "completed",  "odd_spacing": true}\n')
    log = _file(tmp_path, "out.log", "105 passed\n")
    saved = hl.save(logs, "proj", "43.1", "202610", {"worker_report": report, "stdout_log": log})
    d = logs / "proj" / "202610" / "43.1"
    assert (d / "worker-report.json").read_bytes() == report.read_bytes()      # 整形し直さない
    assert (d / "commands.stdout.log").read_bytes() == log.read_bytes()
    assert sorted(saved) == ["commands.stdout.log", "worker-report.json"]


def test_second_candidate_gets_a_number_and_nothing_is_overwritten(tmp_path):
    logs = tmp_path / "logs"
    first = _file(tmp_path, "a.json", '{"n": 1}')
    second = _file(tmp_path, "b.json", '{"n": 2}')
    hl.save(logs, "proj", "1", "202610", {"worker_report": first})
    saved = hl.save(logs, "proj", "1", "202610", {"worker_report": second})
    d = logs / "proj" / "202610" / "1"
    assert json.loads((d / "worker-report.json").read_text()) == {"n": 1}
    assert json.loads((d / "worker-report.2.json").read_text()) == {"n": 2}
    assert saved == ["worker-report.2.json"]


def test_logs_that_accumulate_are_appended_not_numbered(tmp_path):
    logs = tmp_path / "logs"
    hl.save(logs, "proj", "1", "202610", {"stdout_log": _file(tmp_path, "a.log", "run 1\n")})
    hl.save(logs, "proj", "1", "202610", {"stdout_log": _file(tmp_path, "b.log", "run 2\n")})
    text = (logs / "proj" / "202610" / "1" / "commands.stdout.log").read_text(encoding="utf-8")
    assert "run 1\n" in text and "run 2\n" in text and text.index("run 1") < text.index("run 2")


def test_unsafe_names_are_rejected(tmp_path):
    report = _file(tmp_path, "wr.json", "{}")
    for bad in ("../x", "a/b", "", "."):
        with pytest.raises(ValueError):
            hl.save(tmp_path / "logs", bad, "1", "202610", {"worker_report": report})
        with pytest.raises(ValueError):
            hl.save(tmp_path / "logs", "proj", bad, "202610", {"worker_report": report})
    with pytest.raises(ValueError):
        hl.save(tmp_path / "logs", "proj", "1", "2026-10", {"worker_report": report})


def test_invalid_worker_report_json_is_still_saved_but_warned(tmp_path):
    report = _file(tmp_path, "wr.json", "{not json")
    warnings = []
    hl.save(tmp_path / "logs", "proj", "1", "202610", {"worker_report": report}, warn=warnings.append)
    assert (tmp_path / "logs" / "proj" / "202610" / "1" / "worker-report.json").read_text() == "{not json"
    assert warnings and "JSON" in warnings[0]


# --- 実測 --------------------------------------------------------------------

def test_measure_counts_tools_by_kind_from_transcript(tmp_path):
    t = _transcript(tmp_path, [
        _tool_use("Read", file_path="/r/a.py"), _tool_result("1\n2\n3"),
        _tool_use("Read", file_path="/r/a.py"), _tool_result("1"),
        _tool_use("Grep", pattern="x"), _tool_result("hit"),
        _tool_use("Glob", pattern="*.py"), _tool_result("a.py"),
        _tool_use("Bash", command="python -m pytest -q"), _tool_result("ok\n" * 40),
        _tool_use("Bash", command="grep -rn foo src"), _tool_result("hit"),
        _tool_use("Bash", command="cat README.md | head"), _tool_result("x"),
        _tool_use("Edit", file_path="/r/a.py"), _tool_result("ok"),
        _tool_use("Write", file_path="/r/b.py"), _tool_result("ok"),
        _tool_use("mcp__x__search_console_report"), _tool_result("ok"),
    ])
    m = hl.measure(t)
    assert (m["read_calls"], m["search_calls"], m["bash_calls"], m["edit_calls"], m["other_calls"]) == (3, 3, 1, 2, 1)
    assert m["largest_output_lines"] == 40
    assert m["reread_files"] == ["/r/a.py"]
    assert m["data_source"] == "measured"


def test_measure_can_limit_to_sidechain_entries(tmp_path):
    main = _tool_use("Read", file_path="/main.py")
    sub = {**_tool_use("Edit", file_path="/sub.py"), "isSidechain": True}
    m = hl.measure(_transcript(tmp_path, [main, sub]), sidechain_only=True)
    assert (m["read_calls"], m["edit_calls"]) == (0, 1)


def test_measure_does_not_copy_message_text(tmp_path):
    secret = "AKIA-SECRET-VALUE-123"
    t = _transcript(tmp_path, [_tool_use("Bash", command=f"echo {secret}"), _tool_result(secret)])
    assert secret not in json.dumps(hl.measure(t), ensure_ascii=False)


def test_save_with_transcript_writes_measured_usage_next_to_candidate(tmp_path):
    logs = tmp_path / "logs"
    t = _transcript(tmp_path, [_tool_use("Read", file_path="/a"), _tool_result("x")])
    hl.save(logs, "proj", "1", "202610", {"worker_report": _file(tmp_path, "a.json", "{}")}, transcript=t)
    hl.save(logs, "proj", "1", "202610", {"worker_report": _file(tmp_path, "b.json", "{}")}, transcript=t)
    d = logs / "proj" / "202610" / "1"
    assert json.loads((d / "tool-usage.measured.json").read_text())["read_calls"] == 1
    assert (d / "tool-usage.measured.2.json").is_file()
    assert not (d / "agent.jsonl").exists()          # トランスクリプト本体は保存しない


def test_harness_logs_are_gitignored():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".claude/harness-logs/" in text


# --- locate（Worker のトランスクリプトがどこに残るかの判定） --------------------

def test_project_dir_name_matches_claude_code_encoding():
    name = hl.encode_project_dir(r"C:\Users\hiroshi_takizawa\ai-platform-template\.claude\worktrees\paper-x")
    assert name == "C--Users-hiroshi-takizawa-ai-platform-template--claude-worktrees-paper-x"


def _jsonl(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


def test_locate_reports_no_subagent_when_none_ran(tmp_path):
    _jsonl(tmp_path / "s1.jsonl", [_tool_use("Read", file_path="/a")])
    r = hl.locate(tmp_path)
    assert r["layout"] == "no-subagent-yet"
    assert r["files"][0]["tool_use"] == 1 and r["files"][0]["agent_launches"] == 0


def test_locate_detects_separate_subagent_files(tmp_path):
    _jsonl(tmp_path / "s1.jsonl", [_tool_use("Agent", subagent_type="worker")])
    _jsonl(tmp_path / "s1" / "subagents" / "agent-abc.jsonl", [_tool_use("Edit", file_path="/a")])
    r = hl.locate(tmp_path)
    assert r["layout"] == "separate-files"
    assert any(f["path"].endswith("agent-abc.jsonl") for f in r["subagent_files"])


def test_locate_detects_sidechain_entries_in_main_file(tmp_path):
    _jsonl(tmp_path / "s1.jsonl", [_tool_use("Task", subagent_type="worker"),
                                   {**_tool_use("Edit", file_path="/a"), "isSidechain": True}])
    assert hl.locate(tmp_path)["layout"] == "sidechain-in-main"


def test_locate_flags_launch_without_any_subagent_record(tmp_path):
    _jsonl(tmp_path / "s1.jsonl", [_tool_use("Agent", subagent_type="worker")])
    assert hl.locate(tmp_path)["layout"] == "launched-but-not-recorded"


# --- 1段の運用（セッション自身の記録を、タスクの開始時刻以降だけ数える） ------------

def _at(entry: dict, ts: str) -> dict:
    return {**entry, "timestamp": ts}


def test_measure_since_counts_only_entries_after_task_start(tmp_path):
    t = _transcript(tmp_path, [
        _at(_tool_use("Read", file_path="/old"), "2026-10-01T00:00:00Z"),       # 前のタスク
        _at(_tool_use("Edit", file_path="/new"), "2026-10-01T02:00:00Z"),
        _at(_tool_result("a\nb"), "2026-10-01T02:00:01Z"),
        _tool_use("Read", file_path="/no-timestamp"),                            # 時刻なしは数えない
    ])
    m = hl.measure(t, since="2026-10-01T01:00:00+00:00")
    assert (m["read_calls"], m["edit_calls"], m["largest_output_lines"]) == (0, 1, 2)
    assert m["since"] == "2026-10-01T01:00:00+00:00"


def test_measure_since_rejects_bad_time(tmp_path):
    with pytest.raises(ValueError):
        hl.measure(_transcript(tmp_path, []), since="yesterday")


def test_resolve_latest_session_and_latest_subagent(tmp_path):
    import os
    old = tmp_path / "s-old.jsonl"
    new = tmp_path / "s-new.jsonl"
    sub = tmp_path / "s-new" / "subagents" / "agent-1.jsonl"
    for i, p in enumerate((old, new, sub)):
        _jsonl(p, [{}])
        os.utime(p, (1_000 + i, 1_000 + i))
    assert hl.resolve_transcript("latest-session", tmp_path) == new
    assert hl.resolve_transcript("latest-subagent", tmp_path) == sub
    assert hl.resolve_transcript(str(old), tmp_path) == old                 # パスはそのまま


def test_resolve_latest_subagent_errors_when_none(tmp_path):
    _jsonl(tmp_path / "s.jsonl", [{}])
    with pytest.raises(ValueError, match="サブエージェント"):
        hl.resolve_transcript("latest-subagent", tmp_path)


# --- hook（Claude Code のフックから自動で呼ばれる） ------------------------------

def _session(tmp_path: Path, sid: str = "s1") -> tuple[Path, Path]:
    """作業フォルダと、そのセッションのトランスクリプトを作る."""
    cwd = tmp_path / "repo"
    cwd.mkdir(exist_ok=True)
    tr = tmp_path / "projects" / f"{sid}.jsonl"
    _jsonl(tr, [_tool_use("Read", file_path="/a"), _tool_use("Edit", file_path="/a")])
    return cwd, tr


def test_hook_stop_records_session_usage_and_updates_it(tmp_path):
    cwd, tr = _session(tmp_path)
    payload = {"hook_event_name": "Stop", "session_id": "s1", "transcript_path": str(tr), "cwd": str(cwd)}
    out = hl.run_hook(payload, month="202610")
    f = cwd / ".claude" / "harness-logs" / "repo" / "202610" / "session-s1" / "tool-usage.measured.json"
    assert out == f and json.loads(f.read_text())["edit_calls"] == 1
    _jsonl(tr, [_tool_use("Read", file_path="/a"), _tool_use("Edit", file_path="/a"),
                _tool_use("Edit", file_path="/b")])                       # セッションが進んだ
    hl.run_hook(payload, month="202610")
    data = json.loads(f.read_text())
    assert data["edit_calls"] == 2 and data["event"] == "Stop"           # 累計で上書き（派生データのため）


def test_hook_subagent_stop_uses_newest_subagent_file(tmp_path):
    import os
    cwd, tr = _session(tmp_path)
    sub_dir = tr.with_suffix("") / "subagents"
    old, new = sub_dir / "agent-old.jsonl", sub_dir / "agent-new.jsonl"
    _jsonl(old, [_tool_use("Read", file_path="/x")])
    _jsonl(new, [_tool_use("Grep", pattern="y")])
    os.utime(old, (1_000, 1_000))
    os.utime(new, (2_000, 2_000))
    payload = {"hook_event_name": "SubagentStop", "session_id": "s1", "transcript_path": str(tr), "cwd": str(cwd)}
    out = hl.run_hook(payload, month="202610")
    assert out.parent.name == "agent-new"
    assert json.loads(out.read_text())["search_calls"] == 1


def test_hook_prefers_agent_transcript_path_when_given(tmp_path):
    cwd, tr = _session(tmp_path)
    agent = tmp_path / "elsewhere" / "agent-zz.jsonl"
    _jsonl(agent, [_tool_use("Glob", pattern="*")])
    payload = {"hook_event_name": "SubagentStop", "session_id": "s1", "transcript_path": str(tr),
               "agent_transcript_path": str(agent), "cwd": str(cwd)}
    assert hl.run_hook(payload, month="202610").parent.name == "agent-zz"


def test_hook_ignores_other_events_bad_payloads_and_opt_out(tmp_path, monkeypatch):
    cwd, tr = _session(tmp_path)
    assert hl.run_hook({"hook_event_name": "PreToolUse", "cwd": str(cwd)}) is None
    assert hl.run_hook({"hook_event_name": "Stop", "cwd": str(cwd)}) is None            # transcript なし
    monkeypatch.setenv("HARNESS_LOG_DISABLE", "1")
    payload = {"hook_event_name": "Stop", "session_id": "s1", "transcript_path": str(tr), "cwd": str(cwd)}
    assert hl.run_hook(payload) is None


def test_hook_cli_never_fails_even_on_garbage(monkeypatch, capsys):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("this is not json"))
    assert hl.main(["hook"]) == 0                                       # セッションを止めない
    assert "harness_log hook" in capsys.readouterr().err


def test_hook_project_name_can_be_overridden(tmp_path, monkeypatch):
    cwd, tr = _session(tmp_path)
    monkeypatch.setenv("HARNESS_LOG_PROJECT", "my-proj")
    payload = {"hook_event_name": "Stop", "session_id": "s1", "transcript_path": str(tr), "cwd": str(cwd)}
    assert hl.run_hook(payload, month="202610").parts[-4] == "my-proj"
