---
name: utility-scored-memory
description: Use when improving harness-logs retrieval, the Self-Tuning Harness Loop, or any agent memory that should learn from success/failure feedback — turns a passive log store into a utility-scored (Q-value) episodic memory à la MemRL, without touching model weights.
---

# Utility-Scored Episodic Memory（有用度スコア付き記憶）

> **出典**: MemRL — *Self-Evolving Agents via Runtime Reinforcement Learning on Episodic Memory*（arXiv:2601.03192, 2026-02 / SJTU ほか）。コード: https://github.com/MemTensor/MemRL
> **適用先**: `.claude/harness-logs/` の検索改善、CLAUDE.md セクション7「Self-Tuning Harness Loop」、Worker の過去解法参照、advisor 相談の学習。

## いつ使うか

- harness-logs を「溜めるだけ」から「使うほど賢くなる」記憶に格上げしたいとき。
- Worker / Reviewer が過去エピソードを引くとき、「意味が似ている」だけでノイズを拾って困っているとき。
- 同一 `escalation_reason` / advisor `reason_code` の再発（harness-retro 起動条件）を能動的に減らしたいとき。
- モデルを強くする前に、まず記憶の使い方で性能を上げたいとき（[harness-retro.md](../../rules/harness-retro.md) 提案4と同じ順序）。

## 中心となる考え方（3点）

MemRL は **推論（凍結LLM）と記憶（学習する層）を分離**する。重みは一切更新せず、記憶の「選び方」だけを強化学習で鍛える。

### 1. Intent-Experience-Utility トリプレット
記憶を key-value ではなく 3 つ組で持つ:

| 要素 | harness-logs での対応 |
|---|---|
| **Intent（意図）** | task.json の task / task_id / files / mode（クエリ側の埋め込み対象） |
| **Experience（経験）** | worker-report・commands.stdout.log・実際の diff/解法（**逐語で保持**） |
| **Utility（有用度 Q値）** | review 判定と検証結果から算出する 0〜1 のスコア（薄い学習レイヤー） |

> Experience は既存の生ログをそのまま使う。**新しく足すのは Q値フィールドだけ**。これで [memory-curation.md](../../rules/memory-curation.md) の「逐語保存・要約禁止」を守れる。

### 2. Two-Phase Retrieval（2段階検索）
1. **Phase A（類似度リコール）**: まず意味的に近い候補を上位 k1 件集める（従来の RAG）。
2. **Phase B（価値考慮の選抜）**: 候補を学習済み Q値で再ランキングし、上位 k2 件を最終コンテキストに採用。
   - スコア = `(1-α)・zscore(類似度) + α・zscore(Q値)`
   - 「似てるけど過去に役立たなかったノイズ（distractor）」をここで落とす。

### 3. Utility-Driven Update（フィードバックで Q値更新）
エピソード適用 → 結果の報酬 r を受け取り、モンテカルロ流で更新:

```
Q ← Q + η・(r − Q)     # η = 学習率
```

- **報酬 r の作り方（harness-logs）**: review が `APPROVE` かつ検証コマンド全 PASS → r=1.0 / `REQUEST_CHANGES` → r=0.0〜0.3 / 部分成功はスコア按分。
- 成功も失敗も学べる。有用な「惜しい失敗」（転用できる手順を含む失敗）は Q値がそこそこ残る。

## harness-logs への適用手順

1. 各 `harness-logs/.../<task_id>/` に `utility.json` を追加（`{ "intent_embedding_ref": ..., "q": 0.0, "updates": [] }`）。**既存ログは書き換えない**（in-place で Q値ファイルだけ足す）。
2. task 完了時、review.json と commands.stdout.log から報酬 r を算出し、`q ← q + η(r−q)`、`updates[]` に履歴を追記（逐語）。
3. Worker が過去解法を参照するとき、Phase A（類似度）→ Phase B（Q値再ランク）で提示する。
4. `advisor-request` の `reason_code × 解決結果` にも同じ Q値を持たせ、相談前に有効策を提示。

## 推奨パラメータ（論文の実測）

| パラメータ | 推奨値 | 根拠 |
|---|---|---|
| α（Q値の重み） | **0.5** | 意味的関連と有用性のバランス最適（凹型ピーク）。0=ノイズ除去できず頭打ち、1→不安定 |
| k1 / k2（検索幅） | **中程度（例 5 / 3）** | 逆U字。少なすぎ=情報不足、多すぎ=文脈ノイズ |
| 正規化 | **z-score 必須** | 外すと忘却率が 0.041→0.073 に悪化 |
| 類似度ゲート θ | **厳しめ必須** | ノイズ濾過と自己進化の安定性に不可欠 |

## ガードレール（既存ルールとの整合）

- **逐語保存を守る**: Q値は「上に足す薄い層」。Experience 本文を要約・圧縮・削除しない（[memory-curation.md](../../rules/memory-curation.md)）。
- **再編成より検索精度**: 大がかりなフォルダ再編成の代わりに Q値で検索の質を上げる（整理そのものは品質を上げない＝提案2と整合）。
- **単調改善で忘却を防ぐ**: Q値更新は単調改善が理論保証されており、harness-retro の「初期記憶の生存・in-place更新」チェックと同じ思想。
- **cc:* マーカー・禁止事項は対象外**: この記憶学習は Plans.md の `cc:*` や CLAUDE.md 禁止事項には触れない。

## 限界（導入前に知っておく）

- 長い軌跡では step-wise 更新が高分散 → 複数ステップ更新 or 定期的なメモリ統合を検討。
- 複数記憶を同時参照したときの功績配分（credit assignment）が曖昧。
- **タスク間の類似度が低いと効果が薄れ、単なる Reflexion（自己反省）に退化**する。案件が多様なら **案件 slug 単位でメモリを分け、タスク密度を高く保つ**のが有効（論文の産業導入の助言）。

## 実測サマリ（説得材料）

CSR で最強ベースライン(MemP)比 **+3.8%**（探索環境 +6.2%）、転移でも +2.8%、学習 Q値と成功率の相関 **r=0.861**、忘却率 **0.041**（最小）。
