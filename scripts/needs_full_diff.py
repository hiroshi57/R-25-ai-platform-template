"""Lead が Worker の変更の全文 diff を読むべきかを、外部の証跡だけで判定する（提案 C）.

使い方:
    python scripts/needs_full_diff.py --stdout-log <commands.stdout.log>          # 結果を表示
    python scripts/needs_full_diff.py --stdout-log <log> --base origin/main --json
    python scripts/needs_full_diff.py --stdout-log <log> --reviewer-flagged
    python scripts/needs_full_diff.py --stdout-log <log> --security-sensitive

判定に使うのは、git の差分・検証コマンドの生出力・Reviewer の指摘・contract の印だけ。
Worker の自己申告（self_review.verified など）は使わない（CLAUDE.md §7: 自己申告は判定の根拠にしない）。
証跡が無いときは「読む」側に倒す。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

THRESHOLD = 300

GENERATED_DIRS = {"dist", "build"}
GENERATED_NAMES = ("*.lock", "package-lock.json", "pnpm-lock.yaml", "*.min.*")

# 小さな変更でも全文を読むべきパス（認証・秘密情報・権限・CI・インフラ）
SENSITIVE = re.compile(
    r"(^|/)\.env"
    r"|secret|credential|password"
    r"|(^|/)auth[^/]*(/|\.|$)"
    r"|permission|policy"
    r"|(^|/)\.claude/(settings[^/]*\.json|hooks/)"
    r"|(^|/)\.github/workflows/"
    r"|(^|/)infra/",
    re.IGNORECASE,
)

# 検証ログに出ていたら「失敗あり」とみなす目印（pytest / 一般的なテストランナー）
FAILURE = re.compile(r"^(FAILED|ERROR)\b|\b\d+ (failed|errors?)\b|^Traceback \(most recent call last\)",
                     re.MULTILINE)


def _to_int(v: str) -> int:
    return int(v) if v.isdigit() else 0     # バイナリは "-" になる


def parse_numstat_z(raw: bytes) -> list[tuple[int, int, str]]:
    """`git diff --numstat -z` を (追加行, 削除行, 変更後のパス) にする。バイナリは 0 行として数える."""
    parts = raw.decode("utf-8", errors="replace").split("\0")
    out, i = [], 0
    while i < len(parts):
        rec = parts[i]
        i += 1
        if not rec:
            continue
        added, deleted, path = rec.split("\t", 2)
        if path == "":            # 名前変更: 次の2つが 変更前・変更後 のパス
            path = parts[i + 1]
            i += 2
        out.append((_to_int(added), _to_int(deleted), path))
    return out


def is_generated(path: str) -> bool:
    p = PurePosixPath(path)
    if any(part in GENERATED_DIRS for part in p.parts[:-1]):
        return True
    return any(fnmatch(p.name, pat) for pat in GENERATED_NAMES)


def decide(entries: list[tuple[int, int, str]], stdout_log: str | None, threshold: int = THRESHOLD,
           reviewer_flagged: bool = False, security_sensitive: bool = False) -> dict:
    excluded = [p for _, _, p in entries if is_generated(p)]
    counted = [(a, d, p) for a, d, p in entries if not is_generated(p)]
    lines = sum(a + d for a, d, _ in counted)
    sensitive = [p for _, _, p in entries if SENSITIVE.search(p)]

    reasons = []
    if stdout_log is None:
        reasons.append("検証コマンドの生出力（commands.stdout.log）が指定されていない")
    elif not stdout_log.strip():
        reasons.append("検証コマンドの生出力が空")
    elif FAILURE.search(stdout_log):
        reasons.append("検証コマンドの生出力に失敗が含まれる")
    if reviewer_flagged:
        reasons.append("Reviewer が指摘した")
    if security_sensitive:
        reasons.append("contract が security-sensitive")
    if sensitive:
        reasons.append(f"認証・秘密情報・権限・CI・インフラに当たるパスを変更: {sensitive}")
    if lines > threshold:
        reasons.append(f"生成ファイルを除いた変更行数 {lines} 行が {threshold} 行を超える")

    return {"needs_full_diff": bool(reasons), "reasons": reasons, "changed_lines": lines,
            "excluded_files": excluded, "sensitive_files": sensitive}


def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout  # noqa: S603,S607


def _default_base() -> str:
    for ref in ("origin/main", "origin/master", "main", "master"):
        try:
            return _git("merge-base", "HEAD", ref).decode().strip()
        except subprocess.CalledProcessError:
            continue
    raise SystemExit("比べる基準が見つかりません。--base を指定してください")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base", help="比べる基準（既定: origin/main との merge-base）")
    p.add_argument("--stdout-log", type=Path,
                   help="検証コマンドの生出力（harness-logs の commands.stdout.log）")
    p.add_argument("--threshold", type=int, default=THRESHOLD)
    p.add_argument("--reviewer-flagged", action="store_true")
    p.add_argument("--security-sensitive", action="store_true")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)

    base = a.base or _default_base()
    entries = parse_numstat_z(_git("diff", "--numstat", "-z", "-M", f"{base}...HEAD"))
    log = None
    if a.stdout_log is not None:
        log = a.stdout_log.read_text(encoding="utf-8", errors="replace") if a.stdout_log.is_file() else ""
    r = {"base": base, **decide(entries, log, a.threshold, a.reviewer_flagged, a.security_sensitive)}

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(f"全文 diff を読む: {'はい' if r['needs_full_diff'] else 'いいえ（--stat とレポートで判断）'}")
        print(f"生成ファイルを除いた変更行数: {r['changed_lines']}")
        for reason in r["reasons"]:
            print(f"  - {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
