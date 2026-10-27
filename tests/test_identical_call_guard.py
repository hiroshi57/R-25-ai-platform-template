"""identical-call-guard hook の回帰テスト.

仕様: `decision-boundaries.md` ルール1「機械的な検出」（hiroshi57/harness-rules の `.claude/rules/`）
  - 同じツール名・同じ引数の呼び出しの「失敗」を数える。間に別の呼び出し（Edit など）を挟んでも数え続け、
    その呼び出しが成功したら数え直す。直近 50 件より前は数えない
  - 失敗が 5 回 → 注意を 1 回だけ／失敗が 8 回 → 停止
  - 成功した呼び出しは数えない（成功が続いても注意しない）
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK_DIR = ROOT / ".claude" / "hooks"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "identical_call_guard", HOOK_DIR / "identical_call_guard.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


guard = _load_module()


def _payload(tool="Bash", tool_input=None, failed=False, session="s1", event=None):
    tool_input = tool_input if tool_input is not None else {"command": "pytest -q"}
    if event is None:
        event = "PostToolUseFailure" if failed else "PostToolUse"
    p = {
        "session_id": session,
        "hook_event_name": event,
        "tool_name": tool,
        "tool_input": tool_input,
        "tool_response": {"stdout": "", "stderr": ""},
    }
    if failed:
        p["error"] = "command failed"
    return json.dumps(p)


def _run(state_dir, payload, env=None):
    out, code = guard.run(payload, state_dir=state_dir, env=env or {})
    return (json.loads(out) if out else None), code


def _repeat(state_dir, n, **kw):
    results = [_run(state_dir, _payload(**kw)) for _ in range(n)]
    return [r[0] for r in results]


def _is_notice(o):
    return (
        o is not None and "additionalContext" in o.get("hookSpecificOutput", {}) and o.get("continue", True)
    )


def _is_stop(o):
    return o is not None and o.get("continue") is False


# --- 注意 ---------------------------------------------------------------


def test_no_output_below_notice_threshold(tmp_path):
    outs = _repeat(tmp_path, 4)
    assert outs == [None, None, None, None]


def test_identical_successes_never_notice(tmp_path):
    """確認コマンド（gh pr checks など）を成功のまま繰り返しても注意しない."""
    outs = _repeat(tmp_path, 12)
    assert outs == [None] * 12


def test_notice_once_for_five_identical_failures(tmp_path):
    outs = _repeat(tmp_path, 7, failed=True)
    assert [_is_notice(o) for o in outs] == [False, False, False, False, True, False, False]


def test_notice_mentions_rule1_options(tmp_path):
    outs = _repeat(tmp_path, 5, failed=True)
    ctx = outs[4]["hookSpecificOutput"]["additionalContext"]
    assert "decision-boundaries.md" in ctx
    assert outs[4]["hookSpecificOutput"]["hookEventName"] == "PostToolUseFailure"


# --- 停止 ---------------------------------------------------------------


def test_stop_at_eight_identical_failures(tmp_path):
    outs = _repeat(tmp_path, 8, failed=True)
    assert not any(_is_stop(o) for o in outs[:7])
    assert _is_stop(outs[7])
    assert "identical-failing-calls" in outs[7]["stopReason"]


def test_identical_successes_never_stop(tmp_path):
    outs = _repeat(tmp_path, 12)
    assert not any(_is_stop(o) for o in outs)


def test_success_resets_failure_streak(tmp_path):
    _repeat(tmp_path, 7, failed=True)
    _run(tmp_path, _payload(failed=False))
    outs = _repeat(tmp_path, 7, failed=True)
    assert not any(_is_stop(o) for o in outs)


def test_failure_via_tool_response_flag(tmp_path):
    """PostToolUse のまま tool_response 側で失敗を示す形式も失敗として数える."""
    p = json.loads(_payload())
    p["tool_response"] = {"is_error": True}
    outs = [_run(tmp_path, json.dumps(p))[0] for _ in range(8)]
    assert _is_stop(outs[7])


# --- 連続の判定 ---------------------------------------------------------


def _edit(i):
    return _payload(
        tool="Edit", tool_input={"file_path": "a.py", "old_string": f"v{i}", "new_string": f"v{i + 1}"}
    )


def test_retry_loop_with_edits_interleaved(tmp_path):
    """よくあるやり直し（テスト失敗 → Edit → 同じテスト失敗 …）を検出する."""
    fails = []
    for i in range(8):
        fails.append(_run(tmp_path, _payload(failed=True))[0])
        assert _run(tmp_path, _edit(i))[0] is None  # 成功した Edit は数えない
    assert [_is_notice(o) for o in fails] == [False, False, False, False, True, False, False, False]
    assert not any(_is_stop(o) for o in fails[:7])
    assert _is_stop(fails[7])


def test_other_failures_are_counted_separately(tmp_path):
    """別の呼び出しの失敗は、自分の回数には足さない（間に挟まっても数え直しにもならない）."""
    _repeat(tmp_path, 4, failed=True)
    other = [_run(tmp_path, _payload(tool_input={"command": "ls"}, failed=True))[0] for _ in range(4)]
    assert not any(_is_notice(o) for o in other)
    outs = _repeat(tmp_path, 1, failed=True)
    assert _is_notice(outs[0])  # pytest の5回目


def test_success_between_resets_count_even_when_interleaved(tmp_path):
    for i in range(7):
        _run(tmp_path, _payload(failed=True))
        _run(tmp_path, _edit(i))
    _run(tmp_path, _payload(failed=False))  # 一度通った
    outs = []
    for i in range(7):
        outs.append(_run(tmp_path, _payload(failed=True))[0])
        _run(tmp_path, _edit(100 + i))
    assert not any(_is_stop(o) for o in outs)


def test_failures_older_than_window_are_not_counted(tmp_path):
    _repeat(tmp_path, 4, failed=True)
    for i in range(guard.MAX_HISTORY):
        _run(tmp_path, _edit(i))
    outs = _repeat(tmp_path, 1, failed=True)
    assert outs == [None]


def test_argument_key_order_does_not_matter():
    a = guard.call_key("Edit", {"file_path": "a", "old_string": "x"})
    b = guard.call_key("Edit", {"old_string": "x", "file_path": "a"})
    assert a == b
    assert a != guard.call_key("Edit", {"file_path": "a", "old_string": "y"})


def test_sessions_are_isolated(tmp_path):
    _repeat(tmp_path, 4, session="A", failed=True)
    outs = _repeat(tmp_path, 1, session="B", failed=True)
    assert outs == [None]


def test_thresholds_configurable_by_env(tmp_path):
    env = {"IDENTICAL_CALL_NOTICE": "2", "IDENTICAL_CALL_STOP": "3"}
    outs = [_run(tmp_path, _payload(failed=True), env)[0] for _ in range(3)]
    assert _is_notice(outs[1])
    assert _is_stop(outs[2])


# --- 秘密の扱い（secret-isolation.md ルール6） -------------------------


def test_state_stores_hash_not_arguments(tmp_path):
    secret = "sk-THISISNOTAREALKEY1234567890"
    _run(tmp_path, _payload(tool_input={"command": f"curl -H 'Authorization: {secret}'"}))
    stored = "".join(p.read_text(encoding="utf-8") for p in tmp_path.rglob("*") if p.is_file())
    assert stored
    assert secret not in stored
    assert "curl" not in stored


def test_retries_log_line_written_on_notice_and_stop(tmp_path):
    log = tmp_path / "retries.log"
    env = {"HARNESS_RETRIES_LOG": str(log)}
    for _ in range(8):
        _run(tmp_path / "state", _payload(failed=True, tool_input={"command": "secret-arg"}), env)
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert "notice" in lines[0] and "stop" in lines[1]
    assert "secret-arg" not in log.read_text(encoding="utf-8")


# --- 安全側に倒す -------------------------------------------------------


@pytest.mark.parametrize("bad", ["", "not json", "[]", '{"tool_name": 1}'])
def test_malformed_input_passes_through(tmp_path, bad):
    out, code = guard.run(bad, state_dir=tmp_path, env={})
    assert out == ""
    assert code == 0


def _bash():
    """settings.json と同じく bash から呼ぶ。Windows では WSL の bash ではなく Git for Windows の bash を使う."""
    if os.name == "nt":
        git = shutil.which("git")
        if git:
            # git.exe は Git/cmd/ や Git/mingw64/bin/ にある。上へたどって Git/bin/bash.exe を探す
            for parent in Path(git).resolve().parents:
                cand = parent / "bin" / "bash.exe"
                if cand.exists():
                    return str(cand)
        pytest.skip("Git for Windows の bash が見つからない（CI の Linux では実行される）")
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash が見つからない")
    return bash


def _run_shell(tmp_path, payload, extra_env=None):
    env = dict(os.environ)
    env.update({"CLAUDE_PROJECT_DIR": str(tmp_path), "IDENTICAL_CALL_PYTHON": sys.executable})
    env.update(extra_env or {})
    # settings.json に登録するのと同じ相対パスで、repo ルートから呼ぶ
    return subprocess.run(
        [_bash(), ".claude/hooks/identical-call-guard.sh"],
        input=payload.encode("utf-8"),
        capture_output=True,
        env=env,
        cwd=ROOT,
        timeout=30,
    )


def test_shell_entrypoint_runs(tmp_path):
    """.sh の入口から呼べること（settings.json に登録するのはこちら）."""
    proc = _run_shell(tmp_path, _payload())
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert (tmp_path / ".claude" / "state" / "identical-call-guard").is_dir()


def test_shell_entrypoint_handles_utf8_and_emits_json(tmp_path):
    """日本語を含む引数（UTF-8）でも壊れず、注意を UTF-8 の JSON で返すこと（Windows の cp932 対策）."""
    payload = _payload(tool_input={"command": "echo 日本語のテスト"}, failed=True)
    env = {"IDENTICAL_CALL_NOTICE": "2"}
    _run_shell(tmp_path, payload, env)
    proc = _run_shell(tmp_path, payload, env)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    out = json.loads(proc.stdout.decode("utf-8"))
    assert "identical-call-guard" in out["hookSpecificOutput"]["additionalContext"]
