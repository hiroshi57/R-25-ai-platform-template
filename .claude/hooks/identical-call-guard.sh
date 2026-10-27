#!/usr/bin/env bash
# identical-call-guard: 同じツール呼び出しの連続を検出する hook の入口。
# 判定の本体は identical_call_guard.py（仕様: hiroshi57/harness-rules の .claude/rules/decision-boundaries.md ルール1）。
#
# settings.json への登録（本 repo は登録済み。他の案件は hiroshi57/harness-rules の提案書 2026-09-28-harness-components-and-self-evolution.md diff 5 を参照）:
#   "PostToolUse":        [{ "matcher": "*", "hooks": [{ "type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/identical-call-guard.sh\"" }] }]
#   "PostToolUseFailure": [{ "matcher": "*", "hooks": [{ "type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/identical-call-guard.sh\"" }] }]
#
# 環境変数（任意）:
#   IDENTICAL_CALL_NOTICE  注意を出す連続回数（既定 5）
#   IDENTICAL_CALL_STOP    止める失敗の連続回数（既定 8）
#   HARNESS_RETRIES_LOG    注意・停止を1行ずつ追記する retries.log のパス
#   IDENTICAL_CALL_PYTHON  使う Python を明示する
#
# Python が見つからない・hook が失敗した場合も exit 0 で通す（ツール実行を妨げない）。

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for py in "${IDENTICAL_CALL_PYTHON:-}" python3 python py; do
  [ -n "$py" ] || continue
  command -v "$py" >/dev/null 2>&1 || continue
  # Windows の python3 は Store の仮の実行ファイルのことがあるので、動くか確かめる
  "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1 || continue
  "$py" "$HOOK_DIR/identical_call_guard.py"
  exit 0
done
exit 0
