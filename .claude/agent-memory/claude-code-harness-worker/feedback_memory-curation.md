---
name: memory-curation-rules
description: How to curate filesystem-based memory/skills/harness-logs in this project — preservation rule, when organizing pays, verbatim-vs-distilled, toolset as a lever. From arXiv:2607.26637.
metadata:
  type: feedback
---

ファイルシステムベースのメモリ（`.claude/agent-memory/`・Claude Code skills・`harness-logs`）を
curate するときの運用ルール。5点。

1. **保存ルール（preservation rule）**: 再編成（フォルダ移動・ファイル統合・見出し再構成）で
   既存本文を要約・圧縮・削除しない。構造だけ変え内容は逐語で移送する。
2. **組織化を目的化しない**: 整理の見返りは「大規模ストアでの検索コスト削減」に限られる（小規模では割に合わない）。
   成果を動かすレバーは順に (a)書き込むエージェント能力 (b)提供形式 (c)ツールセット。整理より先にこの3つを見直す。
3. **逐語 vs 蒸留は consumer 依存**: 強いエージェントには逐語の生ログ、弱いエージェントには蒸留 skill。
   生ログ層(harness-logs)と蒸留層(skills)の二層を両方維持する。
4. **ツールセットはストア形状のレバー**: ツールセット変更はモデル変更と同等にストアの形を変える。
   ストアが劣化したらモデル強度より先にツール構成を疑う。
5. **成長前提の健全性**: 初期記憶は削除せず in-place 編集で生存させる。wholesale な作り直しをしない。
   taxonomy 遵守は成長とともに崩れやすい（最強モデル以外）ので逸脱の増加を早期警告扱いにする。

**Why**: Zhou et al. "Filesystem-Based Memory for LLM Agents"（arXiv:2607.26637, 全59p）が、まさに本ハーネスの
記憶系と同じファイルシステムメモリを初めて体系検証した。実証結果 = 組織化そのものは回答品質を上げない／
最悪の退行は再編成時の黙示的圧縮（保存ルール1つで防げる）／ツールセットがモデルと同等にストアを形作る。
論文の management/search/execution 役割は本ハーネスの Lead/Reviewer/Worker に対応する。

**How to apply**: メモリ・skills・harness-logs を編集/再編成/整理する作業の前に本ルールを適用する。
特に「整理してきれいにしたい」衝動が出たら 2 を思い出し、3レバー（能力/形式/ツール）を先に検討する。
全5ルールが本 repo に反映済み（2026-09-11 承認）:
`.claude/rules/memory-curation.md`（保存ルール＝1・整理の投資判断＝2・記憶の提供形式＝3）と
`.claude/rules/harness-retro.md`（ツールセットのレバー＝4・ストア健全性チェック＝5）。
メモリ/skills/log を扱う作業前にこの2ファイルを参照する。詳細と経緯は [[harness-proposals-filesystem-memory]]。
残作業は提案4のグローバル CLAUDE.md セクション7 本文追記のみ（保護対象・本 repo 外・人間が別途適用）。
グローバル CLAUDE.md セクション7「Self-Tuning Harness Loop（要約せず生ログ蓄積）」と整合する。

[更新: 2026-09-29] `.claude/rules/memory-curation.md` と `.claude/rules/harness-retro.md` は本 repo から削除し、Private リポジトリ `hiroshi57/harness-rules` に移した。参照先は harness-rules 側。
