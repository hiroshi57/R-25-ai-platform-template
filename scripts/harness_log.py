"""Worker の出力を harness-logs に逐語で保存し、ツール使用をトランスクリプトから実測する（提案 D）.

使い方（Lead が Worker の結果を受け取るたびに実行する）:
    python scripts/harness_log.py save --project <slug> --task-id <id> \\
        --task task.json --worker-report worker-report.json --stdout-log out.log \\
        [--review review.json] [--retries retries.log] [--advisor advisor.json] \\
        [--transcript <Worker のトランスクリプト .jsonl>] [--sidechain-only] [--month YYYYMM]
    python scripts/harness_log.py measure --transcript <.jsonl> [--sidechain-only]
    python scripts/harness_log.py locate [--cwd <作業フォルダ>]   # Worker の記録がどこに残るかを判定する

保存先: .claude/harness-logs/<project>/<YYYYMM>/<task_id>/（CLAUDE.md §7 のファイル名に合わせる）
- 中身は整形も要約もせず、そのままコピーする（memory-curation.md ルール4）
- worker-report / review / task / advisor は上書きしない。2件目からは <名前>.<n>.json にする（§7 提案10）
- commands.stdout.log / retries.log は区切り行を入れて追記する
- --transcript を渡すと、ツール呼び出しを数えた tool-usage.measured[.<n>].json を候補の隣に置く。
  これは Worker の自己申告ではなく実測なので、tool_usage（自己申告）より優先して集計に使われる。
  トランスクリプト本体と、メッセージ・コマンド・出力の中身は保存しない（秘密を写さないため）。数とファイルパスだけを残す
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LOGS_DIR = Path(".claude/harness-logs")

# 種類 → (§7 のファイル名, 2件目以降の扱い)
KINDS = {
    "task": ("task.json", "number"),
    "worker_report": ("worker-report.json", "number"),
    "review": ("review.json", "number"),
    "advisor": ("advisor.json", "number"),
    "stdout_log": ("commands.stdout.log", "append"),
    "retries": ("retries.log", "append"),
}
MEASURED_NAME = "tool-usage.measured.json"

READ_TOOLS = {"Read", "NotebookRead"}
SEARCH_TOOLS = {"Grep", "Glob"}
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}
READ_CMDS = {"cat", "head", "tail", "sed", "less", "more", "wc", "type", "get-content"}
SEARCH_CMDS = {"grep", "rg", "find", "fd", "ls", "tree", "dir", "select-string", "get-childitem"}
SETUP_CMDS = {"cd", "export", "set", "pushd", "source", "."}

_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _check(label: str, value: str) -> str:
    if not value or not _SAFE.match(value) or value in {".", ".."}:
        raise ValueError(f"{label} に使えない名前です: {value!r}（英数字・. _ - のみ）")
    return value


def _numbered(directory: Path, name: str) -> Path:
    """name が空いていればそれを、埋まっていれば <stem>.<n><suffix>（n=2,3,...）を返す."""
    first = directory / name
    if not first.exists():
        return first
    stem, suffix = name[: -len(Path(name).suffix)], Path(name).suffix
    n = 2
    while (directory / f"{stem}.{n}{suffix}").exists():
        n += 1
    return directory / f"{stem}.{n}{suffix}"


def _suffix_of(path: Path, base: str) -> str:
    """worker-report.2.json → '.2'、worker-report.json → ''."""
    stem = base[: -len(Path(base).suffix)]
    m = re.match(rf"^{re.escape(stem)}(\.\d+)?{re.escape(Path(base).suffix)}$", path.name)
    return (m.group(1) or "") if m else ""


# --- 実測 --------------------------------------------------------------------

def _shell_kind(command: str) -> str:
    for segment in re.split(r"&&|\|\||;|\|", command or ""):
        words = segment.strip().split()
        while words and "=" in words[0] and not words[0].startswith("-"):
            words = words[1:]                        # FOO=bar cmd の FOO=bar を飛ばす
        if not words:
            continue
        first = words[0].lower().rsplit("/", 1)[-1]
        if first in SETUP_CMDS:
            continue
        if first == "git" and len(words) > 1 and words[1] == "grep":
            return "search"
        if first in SEARCH_CMDS:
            return "search"
        if first in READ_CMDS:
            return "read"
        return "bash"
    return "bash"


def _result_lines(content: object) -> int:
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    else:
        return 0
    return len(text.splitlines())   # 末尾の改行で1行増やさない


def _parse_time(value: str) -> datetime:
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as e:
        raise ValueError(f"時刻は ISO 8601 にしてください（例: 2026-10-01T10:30:00+09:00）: {value!r}") from e
    return t if t.tzinfo else t.astimezone()      # タイムゾーンなしは、この PC の時刻とみなす


def measure(transcript: Path, sidechain_only: bool = False, since: str | None = None) -> dict:
    """トランスクリプト（JSON Lines）からツール呼び出しを種類ごとに数える。中身は写さない.

    since を渡すと、その時刻以降の行だけを数える（1つのセッションで複数のタスクをこなす1段の運用向け）。
    時刻の無い行は、since を渡したときは数えない。
    """
    start = _parse_time(since) if since is not None else None
    counts = Counter()
    reads: Counter[str] = Counter()
    largest = 0
    lines_read = bad_lines = 0
    with Path(transcript).open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                bad_lines += 1
                continue
            lines_read += 1
            if not isinstance(entry, dict) or (sidechain_only and not entry.get("isSidechain")):
                continue
            if start is not None:
                ts = entry.get("timestamp")
                try:
                    if not isinstance(ts, str) or _parse_time(ts) < start:
                        continue
                except ValueError:
                    continue
            message = entry.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            for c in content:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_result":
                    largest = max(largest, _result_lines(c.get("content")))
                    continue
                if c.get("type") != "tool_use":
                    continue
                name = str(c.get("name", ""))
                inp = c.get("input") if isinstance(c.get("input"), dict) else {}
                if name in READ_TOOLS:
                    counts["read_calls"] += 1
                    if isinstance(inp.get("file_path"), str):
                        reads[inp["file_path"]] += 1
                elif name in SEARCH_TOOLS:
                    counts["search_calls"] += 1
                elif name in EDIT_TOOLS:
                    counts["edit_calls"] += 1
                elif name in SHELL_TOOLS:
                    counts[f"{_shell_kind(str(inp.get('command', '')))}_calls"] += 1
                else:
                    counts["other_calls"] += 1
    return {
        "data_source": "measured",
        "read_calls": counts["read_calls"],
        "search_calls": counts["search_calls"],
        "bash_calls": counts["bash_calls"],
        "edit_calls": counts["edit_calls"],
        "other_calls": counts["other_calls"],
        "largest_output_lines": largest,
        "reread_files": sorted(p for p, n in reads.items() if n >= 2),
        "transcript_entries": lines_read,
        "transcript_bad_lines": bad_lines,
        "sidechain_only": sidechain_only,
        "since": since,
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# --- 記録の場所の判定 ------------------------------------------------------------

AGENT_TOOLS = {"Agent", "Task"}


def encode_project_dir(cwd: str) -> str:
    """Claude Code が ~/.claude/projects/ の下に作るフォルダ名（英数字以外を '-' にする）."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def _scan(path: Path) -> dict:
    tool_use = side = launches = 0
    last = None
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(e, dict):
                continue
            last = e.get("timestamp") or last
            if e.get("isSidechain"):
                side += 1
            m = e.get("message")
            content = m.get("content") if isinstance(m, dict) else None
            for c in content if isinstance(content, list) else []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    tool_use += 1
                    launches += c.get("name") in AGENT_TOOLS
    return {"tool_use": tool_use, "sidechain_entries": side, "agent_launches": launches,
            "last_timestamp": last}


LATEST = ("latest-session", "latest-subagent")


def resolve_transcript(spec: str, project_dir: Path) -> Path:
    """--transcript の値を実際のファイルにする.

    latest-session  … 作業フォルダの記録のうち、いちばん新しいセッション（1段の運用で自分自身の記録）
    latest-subagent … いちばん新しいサブエージェント（Worker）の記録（2段の運用）
    それ以外        … ファイルのパスとしてそのまま使う
    """
    if spec not in LATEST:
        return Path(spec)
    d = Path(project_dir)
    if spec == "latest-session":
        files, label = list(d.glob("*.jsonl")), "セッション"
    else:
        files, label = list(d.glob("*/subagents/*.jsonl")), "サブエージェント（Worker）"
    if not files:
        raise ValueError(f"{d} に{label}の記録がありません")
    return max(files, key=lambda p: p.stat().st_mtime)


def locate(project_dir: Path) -> dict:
    """トランスクリプトの置き方を判定する。数だけを返し、中身は読まない.

    layout:
      separate-files            … サブエージェントの記録が別ファイル（subagents/ や agent-*.jsonl）
      sidechain-in-main         … 親のファイルに isSidechain の行として入っている（--sidechain-only で数える）
      launched-but-not-recorded … 起動の記録はあるが、サブエージェント側の記録が見当たらない
      no-subagent-yet           … まだ一度もサブエージェントが動いていない
    """
    files = sorted(Path(project_dir).rglob("*.jsonl"))
    scanned = []
    for p in files:
        info = {"path": p.as_posix(), **_scan(p)}
        info["is_subagent_file"] = "subagents" in p.parts or p.name.startswith("agent-")
        scanned.append(info)
    sub_files = [f for f in scanned if f["is_subagent_file"]]
    launches = sum(f["agent_launches"] for f in scanned)
    side = sum(f["sidechain_entries"] for f in scanned if not f["is_subagent_file"])
    if sub_files:
        layout = "separate-files"
    elif side:
        layout = "sidechain-in-main"
    elif launches:
        layout = "launched-but-not-recorded"
    else:
        layout = "no-subagent-yet"
    return {"project_dir": Path(project_dir).as_posix(), "layout": layout, "agent_launches": launches,
            "subagent_files": sub_files, "files": scanned}


# --- フック（Claude Code から自動で呼ばれる） ------------------------------------

HOOK_EVENTS = {"Stop", "SubagentStop"}


def _slug(value: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]", "-", value).strip(".-")
    return s or "unknown"


def _project_slug(cwd: Path) -> str:
    """プロジェクト名。HARNESS_LOG_PROJECT があればそれ。git なら本体リポジトリ名（worktree でも同じ名前）."""
    import os
    import subprocess

    if os.environ.get("HARNESS_LOG_PROJECT"):
        return _slug(os.environ["HARNESS_LOG_PROJECT"])
    try:
        common = subprocess.run(  # noqa: S603
            ["git", "-C", str(cwd), "rev-parse", "--path-format=absolute", "--git-common-dir"],  # noqa: S607
            capture_output=True, text=True, timeout=5, check=True).stdout.strip()
        return _slug(Path(common).parent.name)
    except (OSError, subprocess.SubprocessError):
        return _slug(Path(cwd).name)


def run_hook(payload: dict, month: str | None = None) -> Path | None:
    """Stop / SubagentStop のフックから呼ばれ、実測を harness-logs に置く。書いたファイルを返す.

    Stop         … セッション全体を数え、session-<セッションID>/tool-usage.measured.json を累計で更新する
    SubagentStop … 終わった Worker の記録を数え、agent-<ID>/tool-usage.measured.json に置く
    実測はトランスクリプトから作り直せる派生データなので、更新時は上書きする（生ログの逐語保存とは別扱い）。
    HARNESS_LOG_DISABLE=1 なら何もしない。
    """
    import os

    if os.environ.get("HARNESS_LOG_DISABLE") == "1" or not isinstance(payload, dict):
        return None
    event = payload.get("hook_event_name")
    tr = payload.get("transcript_path")
    if event not in HOOK_EVENTS or not isinstance(tr, str) or not Path(tr).is_file():
        return None
    cwd = Path(payload.get("cwd") or Path.cwd())

    if event == "Stop":
        target = Path(tr)
        task_id = f"session-{_slug(str(payload.get('session_id') or target.stem))}"
    else:
        agent = payload.get("agent_transcript_path")
        if isinstance(agent, str) and Path(agent).is_file():
            target = Path(agent)
        else:
            subs = list(Path(tr).with_suffix("").glob("subagents/*.jsonl"))
            if not subs:
                return None
            target = max(subs, key=lambda p: p.stat().st_mtime)
        task_id = f"agent-{_slug(target.stem.removeprefix('agent-'))}"

    d = cwd / DEFAULT_LOGS_DIR / _project_slug(cwd) / (month or datetime.now().strftime("%Y%m")) / task_id
    d.mkdir(parents=True, exist_ok=True)
    dest = d / MEASURED_NAME
    usage = {**measure(target), "transcript": target.name, "event": event}
    dest.write_text(json.dumps(usage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dest


# --- 保存 --------------------------------------------------------------------

def save(logs_dir: Path, project: str, task_id: str, month: str, files: dict[str, Path],
         transcript: Path | None = None, sidechain_only: bool = False, warn=None,
         since: str | None = None) -> list[str]:
    _check("project", project)
    _check("task_id", task_id)
    if not re.fullmatch(r"\d{6}", month or ""):
        raise ValueError(f"month は YYYYMM の6桁にしてください: {month!r}")
    unknown = set(files) - set(KINDS)
    if unknown:
        raise ValueError(f"知らない種類です: {sorted(unknown)}")
    warn = warn or (lambda msg: print(f"[warn] {msg}", file=sys.stderr))

    d = Path(logs_dir) / project / month / task_id
    d.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    report_suffix: str | None = None

    for kind, src in files.items():
        name, mode = KINDS[kind]
        data = Path(src).read_bytes()
        if name.endswith(".json"):
            try:
                json.loads(data.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                warn(f"{kind} が JSON として読めません（そのまま保存します）: {src}")
        if mode == "append":
            dest = d / name
            with dest.open("ab") as f:
                if dest.stat().st_size:
                    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    f.write(f"\n===== appended {stamp} from {Path(src).name} =====\n".encode())
                f.write(data)
        else:
            dest = _numbered(d, name)
            dest.write_bytes(data)
            if kind == "worker_report":
                report_suffix = _suffix_of(dest, name)
        if dest.name not in saved:
            saved.append(dest.name)

    if transcript is not None:
        stem, ext = MEASURED_NAME[: -len(".json")], ".json"
        dest = d / f"{stem}{report_suffix}{ext}" if report_suffix is not None else _numbered(d, MEASURED_NAME)
        if dest.exists():
            dest = _numbered(d, MEASURED_NAME)
        usage = {**measure(transcript, sidechain_only, since), "transcript": Path(transcript).name}
        dest.write_text(json.dumps(usage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        saved.append(dest.name)
    return saved


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("save", help="Worker の出力を harness-logs に逐語で保存する")
    s.add_argument("--project", required=True)
    s.add_argument("--task-id", required=True)
    s.add_argument("--month", default=datetime.now().strftime("%Y%m"))
    s.add_argument("--logs-dir", type=Path, default=DEFAULT_LOGS_DIR)
    for kind in KINDS:
        s.add_argument(f"--{kind.replace('_', '-')}", type=Path, dest=kind)
    m = sub.add_parser("measure", help="トランスクリプトからツール呼び出しを数えて表示する")
    lo = sub.add_parser("locate", help="Worker（サブエージェント）の記録がどこに残るかを判定する")

    for sp, required in ((s, False), (m, True)):
        sp.add_argument("--transcript", required=required,
                        help="トランスクリプトのパス、または latest-session（1段: 自分自身の記録）/ "
                             "latest-subagent（2段: いちばん新しい Worker の記録）")
        sp.add_argument("--sidechain-only", action="store_true", help="サブエージェント側の記録だけを数える")
        sp.add_argument("--since",
                        help="この時刻以降だけを数える（ISO 8601。1段の運用でタスクの開始時刻を渡す）")
    for sp in (s, m, lo):
        sp.add_argument("--cwd", default=str(Path.cwd()), help="作業フォルダ（既定: 今のフォルダ）")
        sp.add_argument("--projects-dir", type=Path, default=Path.home() / ".claude" / "projects")

    sub.add_parser("hook", help="Claude Code のフック（Stop / SubagentStop）から呼ぶ。標準入力の JSON を読む")

    a = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if a.cmd == "hook":
        # フックの失敗でセッションを止めない: どんな失敗でも終了コード 0 で返し、理由だけ stderr に出す
        try:
            dest = run_hook(json.loads(sys.stdin.read() or "{}"))
            if dest is not None:
                print(f"harness_log hook: {dest.as_posix()}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"harness_log hook: skipped ({type(e).__name__}: {e})", file=sys.stderr)
        return 0
    project_dir =a.projects_dir / encode_project_dir(str(Path(a.cwd).resolve()))
    if a.cmd == "locate":
        if not project_dir.is_dir():
            raise SystemExit(f"トランスクリプトのフォルダがありません: {project_dir}")
        r = locate(project_dir)
        r["files"] = r["files"][-10:]          # 多いときは新しい順に10件だけ表示
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    try:
        transcript = resolve_transcript(a.transcript, project_dir) if a.transcript else None
        if a.cmd == "measure":
            usage = {**measure(transcript, a.sidechain_only, a.since), "transcript": transcript.name}
            print(json.dumps(usage, ensure_ascii=False, indent=2))
            return 0
    except ValueError as e:
        raise SystemExit(str(e)) from e

    files = {k: getattr(a, k) for k in KINDS if getattr(a, k) is not None}
    if not files and a.transcript is None:
        raise SystemExit("保存するファイルを1つ以上指定してください")
    try:
        saved = save(a.logs_dir, a.project, a.task_id, a.month, files, transcript, a.sidechain_only,
                     since=a.since)
    except (ValueError, OSError) as e:
        raise SystemExit(str(e)) from e
    print(json.dumps({"dir": (a.logs_dir / a.project / a.month / a.task_id).as_posix(), "saved": saved},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
