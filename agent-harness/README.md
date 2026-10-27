# agent-harness — 5分で6層すべてを動かす

依存パッケージゼロの Node.js（v18+）で「ハーネス・エンジニアリング 6層」を最小構成で実装したデモ。
`AGENTS.md` の対応表を参照。

## 実行

```bash
node agent-harness/src/harness.mjs
```

### 期待される出力（決定的）

```
[step 1] write_workspace -> ok
  ❌ 失敗: [{"name":"date_format",...},{"name":"fixture_match",...}]
  🔧 限定修復 1/1 回目へ
[step 2] write_workspace -> ok
  ✅ 全チェック通過: output_schema, date_format, fixture_match, no_new_dependency
[step 3] send_message -> paused_for_human_approval
  ⏸ 承認待ち: send_message

=== stop_reason: human_approval_required / retries: 1 ===
```

実行すると `state/current.json`（層4）と `runs/traces.jsonl`（層6）、
`artifacts/export.csv`（成果物）が生成される。

### 状態パッチ（層4）

状態の更新はすべて `src/state-patch.mjs` の `applyPatch` 経由で行う。モデルに状態を丸ごと書き直させない。

- パッチに書かれていないキーは残る（既存キーを落とす上書き事故が起きない）
- 削除は `null` を明示したときだけ
- 追記型の配列は既定で追加のみ。訂正したいときだけ `{ "$set": [...] }` で置き換える
- 追記型の配列は `maxItems` で頭打ちにし、古いものから捨てる（状態が試行回数に比例して伸びない）
- 型違反・未知のキー・変更不可キーの変更を含むパッチは、**一部だけ適用せず**状態を変えずに差し戻す
- 配列は要素の型（`items`）まで検査する。キーの並び順が違う同じ内容は重複とみなす

再試行で同じ修復を繰り返さないよう、試した仮説は `state.tested_hypotheses` に残してコンテキストへ渡す。
`propose()` は「何を試すか」をこの記録から決めるので、記録を隠すと同じ失敗を繰り返す（「★自分で壊して学ぶ」5）。

> 着想は arXiv:2608.26263（SKILL.state）だが、**同論文が示す効果量は根拠として採用していない**。
> 理由は `src/state-patch.mjs` の冒頭コメントを参照。

パッチが差し戻されたら、作り直させて再試行する（既定3回）。3回とも通らなければ、例外で落とさず
エスカレーションで終える。本物のモデルを繋ぐときは、作り直しの中でモデルを呼び、差し戻し理由をプロンプトに入れる。

```bash
node --test agent-harness/test/*.test.mjs
```

### プロンプトの伸び方を実測する

「履歴を毎回送らなければ、プロンプトが履歴の長さに依存しない」を手元で確かめる。

```bash
node agent-harness/bench/context-growth.mjs
node agent-harness/bench/context-growth.mjs 400 1600   # 地平線は引数で指定できる
```

A と B には**同じ手順書（`SPEC`）**を渡す。手順書は固定文字列なので、repo の文書を編集しても測定値は動かない。

実測（決定的。1回あたりの文字数）:

| T | A: 状態のみ（最大） | B: 履歴を全部渡す（最大） | 累計比 B/A |
|---|---|---|---|
| 10 | 548 | 1,112 | 1.5x |
| 50 | 1,225 | 4,806 | 3.2x |
| 200 | 1,798 | 18,812 | 6.4x |
| 1600 | 1,820 | 151,318 | 42.8x |

- A は **1,800 文字あたりで頭打ち**になる（T=200 以降ほぼ変わらない）。B は T に比例して伸びる
- 測っているのはプロンプトの伸び方だけ。**正解率については何も言えない**
- 文字数であってトークン数ではない。実モデルでの比は多少ずれる

> **測定をやり直した経緯** [2026-10-06]: 最初の版は **A にだけ手順書が入り、B には入っていなかった**。
> 不公平な比較だったため「T=10 では A が不利（0.7 倍）」という結果が出ていた。
> 両方に同じ手順書を渡して測り直したところ**逆転し、T=10 でも A が有利（1.5 倍）**になった。
> 以前の「短い手順では不利」「損益分岐は T=25 付近」は**誤りなので取り下げる**。

## 案件を自動で「分類 → 6層処理」する（実業務・実ツール・ドライラン）

複雑な案件を **層0の分類器** が type / risk / split に仕分け、種類に応じた6層処理へ
自動で振り分ける司令塔。実業務1件（**ヨシケイ CRレポート**）を実ツール
（`orchestrator.py` / Vercel 読取）に**ドライラン**で接続している。

```bash
node agent-harness/src/orchestrate.mjs
```

処理の流れ:

| 層 | 動き | 実装 |
|---|------|------|
| 0 分類 | 案件を type=DATA / risk=high / split=9単位 と判定 | `src/classifier.mjs` |
| 1 契約 | type ごとに done_when を確定 | `src/orchestrate.mjs` |
| 2 文脈 | 読取専用ツール(`vercel_read`)で環境確認（automatic） | `src/adapters.mjs` |
| 5 検証 | type 別の証拠ゲート（DATAはドライランで前提条件） | `src/verifiers.mjs` |
| 3 ゲート | risk=high の破壊的操作は**承認で停止・自動実行しない** | `src/policy.mjs` |
| 4/6 | `state/cases.json` と `runs/traces.jsonl` に記録 | `src/state.mjs` |

### 安全規則（MUST）

- **`orchestrator.py` は絶対に自動実行しない**（顧客Excelを書き換えるため）。
  ドライランは「コマンド構築＋入力検証(読取)＋ログ」に限定。実行は人間承認後に人手で。
- **Vercel は読取専用**（`vercel projects ls` 等）。deploy は本番禁止のため実装しない。
- **別リポジトリ(`yosikei-agents`)へは一切書き込まない**。
- 実行させたい場合は、承認後に `state/cases.json` の `planned_command` を人間がコピーして実行する。

## ★自分で壊して学ぶ

読むだけでは身につかない。1つずつ壊して、なぜ壊れるかを確かめる。

1. **証拠を消す（層5）**: `src/verify.mjs` の `date_format` チェック行を削除して再実行。
   → `fixture_match` だけが落ち、証拠から「日付形式」が消えるためモデルは修復を空振りし、
     エスカレーションする。検証の粒度が修復可能性を決めている。

2. **契約を変える（層1）**: `contracts/feature_042.json` の `max_repairs` を `0` にして再実行。
   → 1回も修復せずエスカレーション。「何回粘るか」はモデルでなく契約が決めている。

3. **権限を変える（層3）**: `tools/permissions.json` の `send_message` の `mode` を
   `automatic` にして再実行。→ 承認待ちにならず送信まで走る。モデルのコードは1行も
   変えていないのに振る舞いが変わる（提案・許可・実行の分離）。

4. **コンテキストを削る（層2）**: `src/context.mjs` の `last_evidence` 行を消して再実行。
   → モデルが失敗の証拠を「見られなく」なり、同じ間違いを繰り返してエスカレーション。
     再試行がやめられないのはモデルのせいではなく、環境が証拠を返していないから。

5. **試した記録を消す（層4）**: `src/context.mjs` の `tested_hypotheses` 行を消して再実行。
   → 「直す必要がある」ことは分かるのに「もう試して駄目だった」が分からず、同じ日付形式を
     選び直してエスカレーションする。証拠（4）と試行履歴（5）は別物で、**両方ないと前に進めない**。
     会話履歴を丸ごと渡さなくても、この1項目を構造化して残せば足りる。

## ディレクトリ

```
agent-harness/
├── AGENTS.md                 # 小さな地図（層2）
├── contracts/
│   ├── feature_042.yaml      # 人間が読む契約
│   └── feature_042.json      # コードが読む契約（層1）
├── context/product-rules.md  # ISO 8601 を要求するルール
├── tools/permissions.json    # 権限のはしご（層3）
├── checks/fixture.csv        # 受け入れ正解データ（層5）
├── src/
│   ├── context.mjs           # 層2 コンテキスト・コンパイラ
│   ├── model.mjs             # モデルの代役（決定的）
│   ├── gateway.mjs           # 層3 ツール・ゲートウェイ
│   ├── verify.mjs            # 層5 証拠ゲート
│   ├── state.mjs             # 層4 永続状態 ＋ 層6 トレース
│   ├── state-patch.mjs       # 層4 状態パッチの検証・マージ・rollback
│   └── harness.mjs           # ループ本体
├── state/current.json        # 実行で生成
├── runs/traces.jsonl         # 実行で生成
├── artifacts/export.csv      # 成果物（実行で生成）
├── test/state-patch.test.mjs  # node --test
└── lessons/harness-updates.md
```
