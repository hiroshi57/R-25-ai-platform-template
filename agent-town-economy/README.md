# Agent Town Economy — 論文プラットフォームの再現実装

**元論文**: Regmi, Pudasaini, Pun (Karela Technologies Inc.), *"But How Would AI Agents Run a Town's Economy?"*
arXiv:[2609.11108](https://arxiv.org/abs/2609.11108) [cs.MA], 2026.

論文の**シミュレーションプラットフォーム本体は非公開**（公開されているのは生データのみ）。
本リポジトリは、論文の記述からプラットフォームを**動作する形で再現**したもの。実LLMやAPIキーが
無くても end-to-end で走り、論文が報告した定性的メカニズム（貨幣伝播の失敗・価格硬直・windfall の
退蔵・horizon 依存性）を、検証可能な形で再現する。

## 再現の忠実度マップ

| 論文の要素 | 本実装 | 忠実度 |
|---|---|---|
| 貨幣保存の二重簿記台帳（整数NPR・ドリフトゼロ） | `sim/money.py` | ◎ 完全再現 |
| 2つの時計（世界パルス＋非同期エージェント） | `sim/engine.py` | ◎ |
| 19ツール interface（経済/記憶/社交/移動） | `sim/tools.py` | ○ 10ツールは論文明記・残りは推定（`INFERRED`明記） |
| 二重検証（保存則＋エージェント単位の台帳照合） | `analysis/validate.py` | ◎ |
| 処置条件（観光sweep/ショック/現金給付） | `sim/conditions.py` | ◎ |
| 指標（Gini・順位持続・五分位流動・賃金伝播・マージン分解・MPC） | `analysis/metrics.py` | ◎ |
| 実在ポカラ湖畔のOSM地理 | 合成グリッド既定／**実OSM投入対応**（`sim/osm.py`） | ○ Overpass取得＋GeoJSONローダ＋メートル投影 |
| LLM意思決定方策 | ヒューリスティック既定／**実LLM接続対応**（`sim/llm_backend.py`） | ○ OpenAI互換API・vLLM対応・依存ゼロ |

## 使い方

```bash
cd agent-town-economy

# ベースライン（336パルス = 2週間）を1本走らせて検証＋指標を出す
python run.py --condition baseline --pulses 336 --seed 1

# 現金給付条件（MPC = 限界消費性向を出力）
python run.py --condition wealth-grant --pulses 336 --seed 1

# 観光 low/high スイープ → マージン分解（extensive × intensive）
python run.py --sweep --pulses 336 --seeds 3

# 記憶アブレーション arm（記憶を消しても経済指標が動かないことの確認）
python run.py --condition baseline --pulses 336 --no-memory

# テスト（貨幣保存・台帳照合・指標・OSM・LLM）
python -m pytest -q
```

## 実OSM地理の投入

実在ポカラ湖畔の店舗フットプリントを OpenStreetMap（Overpass API）から取得し、
lat/lon をメートルに投影して合成グリッドを置き換える。OSMに価格は無いため、
**実地理の上に価格付きメニューを合成**する（＝実地理・合成経済）。

```bash
# 1. Overpass から取得（要ネットワーク・1回だけ）
python data/fetch_osm.py                      # -> data/lakeside.geojson

# 2. 取得した GeoJSON でシミュレーション（以後オフライン）
python run.py --condition baseline --osm-geojson data/lakeside.geojson
```

- bbox は `sim/osm.POKHARA_LAKESIDE_BBOX`（`--south/--west/--north/--east` で変更可）。
- 任意地域の GeoJSON（Point / Polygon）をそのまま読める（`sim.osm.load_places_from_geojson`）。
- OSMタグ（amenity/shop/tourism）を7カテゴリにマップ。

## 実LLM方策の接続

`LLMPolicy` に任意の **OpenAI互換エンドポイント**（OpenAI本体／自己ホスト vLLM＝論文の
Qwen・gpt-oss構成）を接続できる。依存パッケージ不要（stdlibの `urllib` のみ）。

```bash
export LLM_API_KEY=sk-...
python run.py --condition baseline --pulses 60 \
    --llm-base-url https://api.openai.com/v1 --llm-model gpt-4o-mini

# 自己ホスト vLLM の例（論文構成に近い）
python run.py --condition baseline --pulses 60 \
    --llm-base-url http://localhost:8000/v1 --llm-model Qwen/Qwen3.8-27B-FP8
```

- 各エージェント活性化ごとに観測（財布・銀行・視界内施設・シフト状況）を渡し、モデルは
  `{"tool": "<name>"}` を返す。パース失敗・不正ツール名は**フォーフィット**してヒューリスティックに
  フォールバック（論文の「malformed generations are forfeited, not retried」を踏襲）。
- `LLMPolicy.malformed` に forfeit 数が記録される。
- これで論文が未検証とした問い——**「賃上げを促すようなプロンプト/モデルなら伝播失敗は崩れるか」**——を
  実モデルで検証できる。

## 実験装置としての使い方（ツマミ）

`HeuristicPolicy` の3つのパラメータが論文の主要メカニズムに直結する。値を変えると指標が動く：

| ツマミ | 意味 | 論文の値 | 上げると |
|---|---|---|---|
| `--reprice-prob` | オーナーが価格改定する確率 | 実測 品目の約0.3%のみ改定 | 価格が需要に反応し始める |
| `--wage-raise-prob` | オーナーが賃金を上げる確率 | 実測 12倍需要でも賃金フラット | 賃金伝播が回復する |
| `--mpc` | 財布からの限界消費性向 | 実測 3〜4%（人間は20〜50%） | windfall が循環し始める |

論文の主張「伝播失敗はプロンプト変更で崩れるはず（未検証）」を、このツマミや `LLMPolicy`
（`callable(prompt)->tool名` を渡すだけ）で実際に試せる。

## 再現された論文の所見（スモーク実行より）

- **貨幣保存**: 全取引が zero-sum、エージェント単位の台帳照合ミスマッチ 0（`all_passed: true`）。
- **価格硬直**: 改定品目 約0.15〜0.40%（論文 ~0.3%）。
- **順位持続（2週間 horizon）**: Spearman ≈ 0.97（論文 0.964）、五分位移動 ≈ 12〜24%（論文 21%）。
- **windfall 退蔵**: MPC ≈ 0（ゼロと区別不能、論文 3〜4%）。
- **賃金伝播の崩壊**: 収益に占める賃金シェアが観光増で低下（low→high）。論文の 0.90→0.20 と同方向。
- **マージン分解の恒等式**: extensive × intensive = revenue multiplier が丸め誤差内で厳密一致。

## ディレクトリ構成

```
agent-town-economy/
├── sim/
│   ├── money.py        # 貨幣保存の二重簿記台帳（真実の単一情報源）
│   ├── world.py        # 762施設・3,981品・視認半径80m
│   ├── agents.py       # エージェント状態＋永続記憶（アブレーション対応）
│   ├── tools.py        # 19ツール interface
│   ├── policy.py       # ヒューリスティック方策＋LLM方策
│   ├── llm_backend.py  # OpenAI互換バックエンド（vLLM対応・依存ゼロ）
│   ├── osm.py          # 実OSM取得・GeoJSONローダ・メートル投影
│   ├── conditions.py   # 処置条件（baseline/low/high/shock/grant）
│   └── engine.py       # 2つの時計のパルスループ
├── analysis/
│   ├── validate.py     # 独立再計算による検証
│   └── metrics.py      # Gini/持続/流動/賃金伝播/マージン分解/MPC
├── data/
│   └── fetch_osm.py    # Overpass から footprint を取得
├── tests/              # 貨幣保存・台帳照合・指標・OSM・LLM の pytest
└── run.py              # CLI エントリポイント
```

## 忠実度の限界（重要）

- LLMを繋がない既定の方策は、論文の**観測された挙動をパラメータで再現**する構成のため、
  「LLMがこう振る舞う」ことの証明ではない。本実装の価値は、**貨幣保存台帳・2つの時計・
  検証パイプライン・分析指標**という論文の方法論的貢献を忠実に再現し、その上で任意の LLM 方策を
  差し込んで検証できる**実験装置**を提供する点にある。
- 地理は合成。実OSM footprint は `sim/world.World._build_registry` を差し替えれば投入可能。
- 賃金決済は簡略化（毎パルス定額）。賃金シェアの水準は論文と厳密一致しないが、観光増に対する
  **低下方向**は再現する。
