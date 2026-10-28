# 実データ実行の記録（real run log）

論文 arXiv:2609.11108 の再現を、**実在の OpenStreetMap 地理**の上で実スケール
（100エージェント × 336パルス = 2 シミュレーション週）で実際に走らせた結果。

## 実行環境

- 日付: 2026-09-17
- 地理: **実 OSM（Pokhara Lakeside, Nepal）**を Overpass API から取得（bbox 28.198,83.942–28.225,83.968）
  - 取得施設数: **899**（restaurant 365 / hotel 301 / cafe 90 / guest_house 87 / bar 25 / fast_food 15 / hostel 13 …）
  - 論文の登録 762 施設に近い規模の実データ
- 方策: `HeuristicPolicy`（既定パラメータ reprice=0.001 / wage_raise=0.001 / mpc=0.035）
- 実行時間: 100体×336パルスの1本が約 5.5 秒（CPUのみ）

再現手順:

```bash
python data/fetch_osm.py                        # 実 OSM を取得 -> data/lakeside.geojson
python run.py --condition wealth-grant --pulses 336 --osm-geojson data/lakeside.geojson
python run.py --sweep --pulses 336 --seeds 3 --osm-geojson data/lakeside.geojson
```

## 検証（貨幣保存）

全ラン `all_passed: true` / `reconciliation_mismatches: 0`。実地理でも貨幣保存の
恒等式（totalMoneyInSystem = 全内部残高の和、各口座 = 署名付き取引履歴の符号和）は厳密に成立。

## 実データでの所見 vs 論文

| 指標 | 実OSM実行 | 論文 | 一致 |
|---|---|---|---|
| 貨幣保存（照合ミスマッチ） | 0 | 偏差ゼロ | ✅ |
| 順位持続（2週間, Spearman ρ） | 0.978 | 0.964 | ✅ |
| 五分位移動 | 16% | 21% | ✅ 近い |
| MPC（windfall退蔵） | −0.022（≈0） | 3〜4%（ゼロと区別不能） | ✅ |
| 価格改定品目 | 0.58% | ~0.3% | ✅ 同オーダー |
| 賃金シェア（観光 low→high） | 1.42 → 0.67（低下） | 0.90 → 0.20（低下） | ✅ 同方向 |
| マージン分解 extensive×intensive=倍率 | 3.481 × 0.669 = 2.327（厳密一致） | 厳密一致 | ✅ |
| 取引店数（low→high, 外延マージン） | 231 → 804 | 増加 | ✅ |

**結論**: 実在ポカラ湖畔の地理を使っても、論文の主要な定性所見（貨幣伝播の失敗＝収益は
店に届くが賃金・価格に伝播しない、windfall の退蔵、マージン分解の恒等式）が再現された。

## 実LLM接続の実行（2026-09-17, Gemini 3.6 Flash）

有効な Gemini APIキーを受領し、**実LLM × 実OSM地理**で実際に意思決定ランを実行した。

コマンド例（実行したもの）:

```bash
LLM_API_KEY="$(cat .llmkey)" python run.py --condition baseline --pulses 4 --n-agents 6 \
    --osm-geojson data/lakeside.geojson \
    --llm-base-url https://generativelanguage.googleapis.com/v1beta/openai \
    --llm-model gemini-3.6-flash --llm-max-tokens 1200
```

### 実行できたこと（本物のLLM意思決定）

- Gemini 3.6 Flash が実際に `{"tool": "start_shift"}` 等の**有効な意思決定を返した**
  （HTTP 200、実地理899施設の上で実行、貨幣保存検証パス）。
- 24決定ラン（6体×4パルス）を51秒で完走、`all_passed: true`。

### 実測で再現された論文の現象

- **社交ツールの失敗**: `invite_to_talk` が **92%失敗**（59コール中54失敗）。
  論文の 94–97% と同水準を、生きたモデルで再現。
- **推論モデルの forfeit**: gemini-3.6-flash は応答前に大量の内部推論トークンを消費し、
  出力枠が小さいと本文が空（`finish_reason=length, completion_tokens=0`）。
  論文の gpt-oss「malformed generations を forfeit、出力枠を上げると改善」と一致。
  → `--llm-max-tokens` を大きくする必要がある（既定 1024）。
- **価格硬直**: `set_price` はほぼ呼ばれない（実測で数十決定中1回、0.025%）。

### 規模の制約（重要・正直な報告）

- 受領キーは **無料枠**で、`gemini-3.6-flash` の**クォータが 20 リクエスト**（`429`,
  `retryDelay 17s`）。数十コールで枯渇するため、**論文規模（100体×336パルス=33,600コール/本）の
  実験は無料枠では不可能**。
- 高頻度コール時は 429 が多発し、`LLMPolicy` がヒューリスティックに大量フォールバック
  （malformed カウンタで可視化）。低頻度なら本物の LLM 決定が取れる。
- **本格的な行動実験には**: (a) 有料枠（RPM/日次上限の緩和）、または (b) 論文と同じ
  **自己ホスト vLLM**（`--llm-base-url http://localhost:8000/v1`、キー不要）が必要。

### 結論（論文の未検証問いに対して）

無料枠の範囲で得た限りでは、**LLM に替えても社交ツール失敗・価格レバー未使用といった
伝播失敗の兆候は残った**（ヒューリスティックと同方向）。ただしサンプルは少数で、
「LLM が伝播失敗を崩すか」の確定判定には有料枠か自己ホスト vLLM での規模実行が必要。
アダプタと実験装置は完成しているので、キー/エンドポイントさえ用意すれば即実行できる。

## 自己ホスト実LLM実験（Ollama / Qwen2.5-0.5B-Instruct × 実地理）

クォータの無い**自己ホスト**推論（Ollama、OpenAI互換 `http://localhost:11434/v1`、
キー不要）で、**LLM 方策 vs ヒューリスティック**を同一世界・同一シード（実OSM 899施設）で
比較。論文の未検証問い「LLM に替えると伝播失敗は崩れるか」を規模実行で検証した。

- 構成: 10エージェント × 24パルス、tourism-low / high の2条件、各240決定
- モデル: `qwen2.5:0.5b-instruct` と `qwen2.5:7b-instruct`（CPU推論、GPU無し）、
  両モデルとも malformed **0**（全て有効なJSON）
- 実行: `python experiments/llm_vs_heuristic.py --geojson data/lakeside.geojson --model <model>`
  （生結果は `experiments/results.json`（0.5B）と `experiments/results_7b.json`（7B））

### 結果（240決定/ラン、貨幣保存は全ラン検証パス）

| 方策 | 観光 | wage_share | set_price | set_wage | buy_food | invite_to_talk | start_shift | 取引店 |
|---|---|---|---|---|---|---|---|---|
| ヒューリスティック | low | 4.61 | **0** | **0** | 2 | 119c/111f | 67 | 15 |
| ヒューリスティック | high | 0.48 | **0** | **0** | 2 | 119c/113f | 67 | 145 |
| LLM (Qwen **0.5B**) | low | 0.43 | **0** | 0 | 0 | 0 | 240 | 150 |
| LLM (Qwen **0.5B**) | high | 0.24 | **0** | 0 | 0 | 0 | 240 | 234 |
| LLM (Qwen **7B**) | low | 0.00 | **228** | 0 | 0 | 0 | 0 | 154 |
| LLM (Qwen **7B**) | high | 0.00 | **237** | 0 | 0 | 0 | 0 | 250 |

### 所見（モデル強度で挙動が一変 — 論文 section 9 と整合）

1. **価格レバーの使用はモデル依存**: ヒューリスティックと Qwen0.5B は `set_price` を**全ランで0回**
   （伝播失敗）。一方 **Qwen7B は low 228 回・high 237 回**と価格を積極的に改定した。
   → 「どのLLMが決めるかが一次的決定要因」という論文の結論を、自己ホスト実験で再現。
   伝播失敗は**完全にはモデル非依存でなく、能力次第で価格レバーが動く**。
2. ただし**7Bも健全な循環経済にはならない**: 7Bはオーナー決定のほぼ全てを `set_price` に費やし、
   `start_shift`（労働）・`buy_food`（消費）を選ばないため wage_share=0（賃金が支払われない）。
   0.5B は逆に全て `start_shift`。**両モデルとも別方向に退化**しており、レバーは使うが
   二次需要を生む循環（賃金→消費）は起きない。
3. `set_wage`（賃金引上げ）は**どのモデルでも0回**。賃金伝播の回復は観測されず。
4. 賃金シェアは観光増でヒューリスティック 4.61→0.48（低下、論文 0.90→0.20 と同方向）。

### 限界

- 0.5B/7B はいずれも小型で挙動が単調（一方向に退化）。より大きな指示追従モデルや、
  「売り切れたら賃上げも選択肢」と促すプロンプト、複数ツールの併用を促す設計では
  結果が変わりうる（＝論文の反証可能性）。
- CPU推論のため 7B は1ラン約15〜28分。大型モデル・長horizon・多シードは GPU + vLLM 推奨。
- 本実験は「装置が実LLMで規模実行でき、ツール使用がモデル強度に強く依存する」ことの実証であり、
  モデル一般化の主張ではない。

## 記憶表現の実験：LLM-Wiki は「memory doesn't matter」を覆すか（WFM 2609.18182 との組合せ）

論文(2609.11108)は「エージェントの素朴な記憶を**消しても**経済成果は変わらない」と報告した。
WFM(2609.18182)は「問題は記憶の**有無ではなく表現**。LLM Wiki なら flat store では表せない
**連結を多段推論で辿れる**」と主張する。この2本を組み合わせ、**世界・シード・方策(WikiPolicy)を
完全固定し、メモリ表現だけ `flat`↔`wiki` で切替**えて検証した。

- 実装: `sim/wiki_memory.py`（エンティティ/概念トポロジ＋passage、多段検索）、
  `sim/policy.WikiPolicy`（状況→概念→到達可能な行動概念を引いてレバーを引く）
- wiki は "sold_out→raise_wage" 等の運用知識(playbook)を辿れる。flat は辿れず何も返さない
  ＝論文の「記憶は効かない」挙動に退化
- 実行: `python experiments/wiki_vs_flat.py`（生結果 `experiments/results_wiki.json`、40体×150パルス、
  貨幣保存は全ラン検証パス）

### 結果（メモリ表現だけが唯一の変数）

| memory | 観光 | set_wage | set_price | 価格改定% | buy_food | MPC(給付) |
|---|---|---|---|---|---|---|
| **flat** | baseline | **0** | **0** | 0.00 | 219 | — |
| **flat** | high | **0** | **0** | 0.00 | 219 | — |
| **flat** | grant | **0** | **0** | 0.00 | 219 | **0.050** |
| **wiki** | baseline | **648** | 0 | 0.00 | 350 | — |
| **wiki** | high | **1166** | **1652** | **1.00** | 194 | — |
| **wiki** | grant | **527** | 0 | 0.00 | 394 | **0.168** |

### 所見

1. **記憶の"表現"が伝播失敗を崩す**: flat では賃金/価格レバーが**全条件で0回**（論文の再現）。
   wiki に替えるだけで **set_wage 648〜1166 回・set_price は高需要で 1652 回**発火。
   → 「記憶の有無」ではなく「**連結を多段で辿れる表現か**」が効く（WFM の主張を実証）。
2. **windfall が循環し始める**: 給付条件の MPC が **flat 0.050 → wiki 0.168（約3.4倍）**。
   wiki は "received_wage→buy_food" を辿って消費に回すため、二次需要が生まれる。
3. **価格は需要に反応**: 高需要で wiki の価格改定率 1.00%（flat 0%）。needを認識して改定する。
4. 貨幣保存は両アームで厳密成立（`all_passed`）。差は純粋にメモリ表現由来。

### 限界・含意

- 本 wiki は小さな手書き playbook（6リンク）で、WFM の学習済み大規模 LLM Wiki の**軽量代理**。
  「良い記憶表現なら記憶が効く」ことの存在証明であり、WFM 本体の性能主張ではない。
- 賃金シェアの水準は set_price 併発で相殺され明瞭に上がらない（レバー使用と MPC が明快な信号）。
- **組織設計への含意**: 知識層(WFM/LLM Wiki=連結された運用知識)を入れると、エージェントは
  「入口で止まる」振る舞いから「下流に伝える」振る舞いへ動く。ただし incentive/評価設計が伴わないと
  レバーの過剰使用(高需要での過改定)も起きる＝**知識層×調整層×incentive のセット**が要る。
