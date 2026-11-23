---
name: harness-measure
description: Use when deciding whether to adopt a harness component change (toolset / planning / context policy / model). Records harness_config alongside each run via harness_log.py and runs paired A/B comparisons of score and real token cost. Also use to check whether enough data has accumulated to compare.
---

# harness-measure — ハーネス構成の計測・対応比較

ハーネスの部品（ツールセット・計画・コンテキスト方針・モデル）を替えるべきかを、
**憶測ではなく対応のある比較**で決める。採否はしない。数値を並べるだけで、結論は
受理ゲート／人間に委ねる。

## 既存ログ層との関係（重複させない）

- `scripts/harness_log.py` が既に、タスクごとに `task.json` / `worker-report.json` /
  `review.json` を逐語保存し、**トランスクリプトから実トークン・実ツール使用を計測**して
  `tool-usage.measured.json` に残す（self-report ではない）。SubagentStop フックで自動。
- 本スキルが足すのは **①run の構成（harness_config）の記録** と **②対応比較** だけ。

## 1. 記録する（タスク完了時）

`harness_log.py save` に `--harness-config` を足す。構成は **Lead が配布時に決める値**で、
Worker の自己申告ではない（CLAUDE.md §7）。

```bash
# この run の構成を書いておく
cat > /tmp/hc.json <<'JSON'
{ "model": "claude-opus-4-8", "toolset": "predefined", "planning": "off", "context_policy": "auto-compact" }
JSON

python scripts/harness_log.py save --project <slug> --task-id <id> \
  --worker-report worker-report.json --review review.json \
  --transcript latest-subagent \
  --harness-config /tmp/hc.json
```

- `harness-config{suffix}.json` が worker-report と**同じ suffix**で保存され、候補と構成が対で読める。
- **対応比較(A/B)**: 比べたい部品だけ（例 `toolset`）を変えて、同じ task-id で**もう一周**記録する。
  他の部品とモデルは固定（harness-components ルール1）。

## 2. 在庫を見る

```bash
python scripts/harness_measure.py summary .claude/harness-logs/<slug>
```

部品ごとの「対になるタスク数」と 20 件到達の見込みが出る。

## 3. 対応比較する

```bash
python scripts/harness_measure.py compare .claude/harness-logs/<slug> \
  --component toolset --a predefined --b action-fusion \
  --model <id> --metric tokens
```

- `--metric`: `tokens`（実トークン・既定）/ `calls` / `largest_output` / `reread`。
- 出力: タスクごとの `Δscore` / `Δcost`（b − a）と中央値、件数不足(<20)の注意。

## 判断境界

- 測るだけ。採用・却下はしない（decision-boundaries）。
- **対が20件未満なら差を結論にしない**（ルール1）。
- 20件以上でも採用は受理ゲート（提案12: δ・コスト規則、提案15: Application/Transfer）＋人間承認を通す。
- モデルを固定して比べる。あるモデルで良い構成を別モデルの既定にしない。

## テスト

```bash
python -m pytest tests/test_harness_measure.py -q
```

## 由来

SoL-Pi（arXiv:2609.20519）の効率機構（Action Fusion / ObservationPack 等）を
「本ハーネスで採用してよいか」を実測で決めるための土台。提案:
`harness-rules/harness-proposals/2026-10-07-sol-pi-token-efficiency-mechanisms.md`。
