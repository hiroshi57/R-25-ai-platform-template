#!/usr/bin/env bash
# harness-log: 返答の終わり（Stop）と Worker の終わり（SubagentStop）に、ツール呼び出しを実測して残す hook の入口。
# 本体は scripts/harness_log.py の hook サブコマンド（仕様: hiroshi57/harness-rules の .claude/rules/harness-retro.md 提案18）。
#
# settings.json への登録（本 repo は登録済み）:
#   "Stop":         [{ "hooks": [{ "type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/harness-log.sh\"", "timeout": 30 }] }]
#   "SubagentStop": [{ "hooks": [{ "type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/harness-log.sh\"", "timeout": 30 }] }]
#
# 残るもの: .claude/harness-logs/<project>/<YYYYMM>/session-<ID>/ と agent-<ID>/ の tool-usage.measured.json
#           （回数とファイルパスだけ。メッセージ・コマンド・出力の中身は残さない。.gitignore 対象）
#
# 環境変数（任意）:
#   HARNESS_LOG_DISABLE=1  何もしない
#   HARNESS_LOG_PROJECT    プロジェクト名を固定する（既定: 本体リポジトリ名）
#   HARNESS_LOG_PYTHON     使う Python を明示する
#
# Python が見つからない・hook が失敗した場合も exit 0 で通す（セッションを止めない）。

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HOOK_DIR/../../scripts/harness_log.py"
[ -f "$SCRIPT" ] || exit 0

for py in "${HARNESS_LOG_PYTHON:-}" python3 python py; do
  [ -n "$py" ] || continue
  command -v "$py" >/dev/null 2>&1 || continue
  # Windows の python3 は Store の仮の実行ファイルのことがあるので、動くか確かめる
  "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1 || continue
  "$py" "$SCRIPT" hook
  exit 0
done
exit 0
