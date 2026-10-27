# agent-harness — 小さな地図（層2）

依存パッケージゼロの Node.js で「ハーネス・エンジニアリング 6層」を最小構成で動かすデモ。
これは *大きな百科事典ではなく、小さな地図* である。詳細は各ファイルに委ね、ここには入口だけ置く。

## 6層と対応ファイル

| 層 | 役割 | ファイル |
|---|------|---------|
| 1 契約 | クエストを機械可読な契約に変換する | `contracts/feature_042.json`（人間用は `.yaml`） |
| 2 コンテキスト | 高信号な情報だけを組み立てる（会話全文を渡さない） | `src/context.mjs` |
| 3 ゲートウェイ | 提案・許可・実行を分離する | `src/gateway.mjs` / `tools/permissions.json` |
| 4 状態・記憶 | 正しく継続するための最低限を残す | `src/state.mjs` / `state/current.json` |
| 5 証拠ゲート | 「良さそう」ではなく証拠で accept / retry / escalate | `src/verify.mjs` / `checks/fixture.csv` |
| 6 トレース | 実行を1行に記録し、失敗を基盤に変える | `src/state.mjs` / `runs/traces.jsonl` |

## 実行

```bash
node agent-harness/src/harness.mjs
```

## タスク（この地図が指す先）

`feature_042`: 分析ダッシュボードに CSV エクスポートを追加する。
- 製品ルール: `context/product-rules.md`（日付は ISO 8601）
- 受け入れ正解: `checks/fixture.csv`
- 権限のはしご: `tools/permissions.json`

## 自分で壊して学ぶ

実験手順は `README.md` の「★自分で壊して学ぶ」を参照。
