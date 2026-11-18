---
name: utility-scored-memory-skill
description: Where the MemRL-derived skill for turning harness-logs into utility-scored (Q-value) episodic memory lives, and when to invoke it.
metadata:
  type: reference
---

MemRL 論文（arXiv:2601.03192）の知見を蒸留したスキルが
`.claude/skills/utility-scored-memory/SKILL.md` にある。

内容: harness-logs を「溜めるだけ」から success/failure フィードバックで自己改善する
Q値付きエピソード記憶へ格上げする実践ガイド（Intent-Experience-Utility トリプレット /
Two-Phase Retrieval / Utility-Driven Update）。凍結LLM・重み更新なし。

**How to apply**: harness-logs の検索改善・Self-Tuning Harness Loop・Worker の過去解法参照・
advisor 相談の学習を扱うとき invoke する。既存の [[memory-curation-rules]]（逐語保存）と
harness-retro（ツールセット優先・単調改善で忘却回避）に整合するよう設計済み。
