# ai-platform-template

AI エージェント / RAG アプリを立ち上げるための社内向け AI 基盤テンプレート。
clone するだけで、認証・レート制限・LLM 切替・観測性・コスト統制が最初から揃う。

> **このリポジトリの位置づけ(正直表記)**
> これは **テンプレート/リファレンス実装** であり、そのまま本番投入できる製品ではない。
> 同梱の単価・品質スコアはハードコードされた初期値で、一次情報による検証は行っていない。
> レート制限と観測性はプロセスローカル、永続化は SQLite。
> スケールアウト構成で使う場合は「[既知の制約](#既知の制約)」を必ず読むこと。

---

## 差別化ポイント: FinOps for LLM

「社内AIの請求書が読める・守れる」ためのコスト統制:

- **月次コスト予測** — 当月の消費ペースから月末着地を外挿(`core/finops.py`)
- **予算アラート** — 着地見込みが予算を超える場合に ok / warn / critical で段階警告
- **予算内ルーティング** — 着地見込みが予算超過なら自動で `cost` 戦略へ降格
  (`AI_PLATFORM_MONTHLY_BUDGET_USD` を設定すると `/v1/chat` に適用される)
- **コスト異常検知** — 直近平均に対する急変を検出(金額下限つきでノイズ抑制)
- **予測信頼度** — サンプル日数が少ない月初は `confidence: low` を返し、
  1日分のスパイクで月中ずっと最安モデルに固定される事故を防ぐ
- API: `GET /v1/budget?budget_usd=...`

加えて基盤としての差別化2点:

1. **コスト/レイテンシ自動ルーティング** — `LLMRouter` が Claude / GPT / Gemini を
   `cost` / `latency` / `quality` / `balanced` の戦略で選択し、失敗時は次点へ自動フォールバック。
   連続失敗したプロバイダはサーキットブレーカで一時降格する。
2. **観測性の標準装備** — 「プロバイダ別コスト」「p50/p95/p99 レイテンシ」
   「フォールバック率」「エラー率」を追加実装なしで集計・API 公開できる。

いずれも **API キーなしで動く rule-based モック** を同梱しているため、clone 直後に動作確認できる。

---

## クイックスタート

```bash
pip install -r requirements.txt

# 1) コア動作確認(APIキー不要)
python -m app_template.demo

# 2) テスト
python -m pytest -q

# 3) API 起動(本番構成: 永続化 + テナント分離 + FinOps)
cp .env.example .env
python -c "from core import generate_api_key; print(generate_api_key())"   # キー生成
# .env の AI_PLATFORM_API_KEYS に "<生成したキー>:<テナント名>" を設定
uvicorn service.api:app --reload

# 4) フロントエンド(別ターミナル)
cd frontend && npm install && npm run dev
```

呼び出し例:

```bash
curl -X POST http://localhost:8000/v1/chat \
  -H "X-API-Key: <生成したキー>" -H "Content-Type: application/json" \
  -d '{"prompt":"経費精算の締め日は?","strategy":"cost"}'
```

> **認証について**: テナントは **サーバが API キーから解決** する。
> クライアントはテナントを指定できない(成りすまし防止)。

---

## 構成

```
core/
  llm_router.py     # ルーティング + フォールバック + サーキットブレーカ
  providers.py      # プロバイダ抽象 + MockProvider(APIキー不要) + トークン推定
  pricing.py        # 単価/モデルカタログの唯一の情報源(外部JSONで差し替え可)
  real_providers.py # 実プロバイダ切替(Claude/GPT/Gemini)。キー無しは自動mock
  observability.py  # コスト/レイテンシ分位/フォールバック率/エラー率の集計
  finops.py         # 月次予測 / 予算アラート / 予算内ルーティング / 異常検知
  notifications.py  # スケジュール通達を Teams/Chatwork へ同報(stdlib のみ・未設定は no-op)
  logging.py        # 構造化(JSON)ロギング + request_id 相関
  config.py         # 設定の一元管理(env)
  auth.py           # APIキー認証(テナント解決)
  rate_limit.py     # テナント別トークンバケット
service/
  api.py            # 本番API(認証/CORS/永続化/レポート)
  db.py             # SQLite 永続化(テナント分離・月次集計)
  report_html.py    # 観測性HTMLレポート
app_template/
  main.py           # 新規アプリの雛形(FastAPI, request-id中間層)
  demo.py           # APIキー不要のCLIデモ
frontend/           # React + Vite(プレイグラウンド + 観測性ダッシュボード)
infra/              # Dockerfile(非root) + Cloud Run manifest
tests/              # 外部依存は fastapi/httpx のみ
```

テスト件数は変動するため README には固定値を書かない。実数は次で確認する:

```bash
python -m pytest --collect-only -q | tail -1
```

---

## 戦略の使い分け

| strategy | 選択基準 | 使いどころ |
|----------|---------|-----------|
| `cost` | 実トークン数で最安 | 大量バッチ・要約 |
| `latency` | 最速 | 対話UI・リアルタイム |
| `quality` | 最高品質 | 重要な意思決定支援 |
| `balanced` | min-max 正規化した合成スコア(既定) | 汎用 |

`balanced` の重みは `LLMRouter(weights={"cost":0.4,"latency":0.3,"quality":0.3})` で調整できる。
3指標はいずれも min-max 正規化されるため、値域の狭い指標(品質など)でも重みが意図どおり効く。

---

## API

| endpoint | 認証 | 説明 |
|----------|------|------|
| `POST /v1/chat` | 要 | ルーティング実行 + メトリクス永続化 |
| `GET /v1/metrics` | 要 | 自テナントの集計 |
| `GET /v1/budget?budget_usd=` | 要 | 月末着地見込み・アラート・異常検知 |
| `GET /v1/report` | 要 | 観測性 HTML レポート |
| `POST /v1/notify/scheduled` | 要 | スケジュール通達を Teams / Chatwork へ同報 |
| `GET /v1/providers` | 不要 | 各プロバイダが real / mock か |
| `GET /healthz` | 不要 | liveness |
| `GET /readyz` | 不要 | readiness(DB 疎通確認) |

### スケジュール通達(Teams / Chatwork 同報)

任意テキストを Teams と Chatwork の両方へ同報する。**外部スケジューラ**(Cloud Scheduler /
cron 等)が `POST /v1/notify/scheduled` を叩く運用を想定している(Cloud Run はスケール0まで
縮むため、アプリ内に常駐スケジューラは持たない)。

- 送信先は env で設定する: `TEAMS_WEBHOOK_URL` / `CHATWORK_API_TOKEN` / `CHATWORK_ROOM_ID`
- **未設定のチャネルは自動で no-op**(clone 直後でも動く。プロバイダの mock フォールバックと同じ思想)
- 片方のチャネル障害でもう片方の配信は止めない(**部分失敗を隔離**)
- 配信結果はチャネル単位で返る。全成功=200 / 一部失敗=502(スケジューラが再送判断できる) / 送信先ゼロ=200 かつ `delivered:false`

```bash
curl -X POST http://localhost:8000/v1/notify/scheduled \
  -H "X-API-Key: <生成したキー>" -H "Content-Type: application/json" \
  -d '{"message":"本日15:00より定例会議です"}'
```

---

## 実プロバイダ接続

`.env` に各社のキーを入れるだけで実 API に切り替わる(キーが無ければ自動で mock)。
`GET /v1/providers` で real / mock を確認できる。

**単価は必ず実測値で上書きすること。** 同梱の単価は未検証の初期値であり、
`GET /v1/providers` の `pricing_unverified` に未検証プロバイダが列挙される。

```bash
export AI_PLATFORM_PRICING_FILE=./pricing.json
```

```json
[
  {"name":"claude","model":"claude-3-5-sonnet-latest",
   "cost_per_1k_input":0.003,"cost_per_1k_output":0.015,
   "avg_latency_ms":900,"quality_score":0.93,
   "key_env":"ANTHROPIC_API_KEY","verified":true}
]
```

---

## 既知の制約

コスト統制を謳う以上、精度の限界を明示する。

| 項目 | 制約 | 影響 |
|------|------|------|
| トークン推定 | mock 実行時は文字種ベースの**近似**(CJK=1文字1トークン等) | 表示コストは実請求と一致しない。実 API 時はレスポンスの `usage` を優先 |
| 単価カタログ | ハードコードされた**未検証**の初期値 | `AI_PLATFORM_PRICING_FILE` で実単価を注入すること |
| 品質スコア | 社内ベンチ想定の**主観値** | `quality` 戦略の順位は自前ベンチで再設定すべき |
| レート制限 | プロセスローカル | N インスタンスで実効上限が N 倍になる |
| 観測性(インメモリ) | プロセスローカル・上限10万件 | 恒久集計は `service/db.py`(SQLite)側を使う |
| SQLite | 単一ノード前提 | Cloud Run 等の揮発環境では外部DBへ差し替えが必要 |
| 月次予測 | 線形外挿 | 月初はサンプル不足。`confidence: low` を確認すること |
| mock のレイテンシ | 決定的な擬似ジッタ | 実測値ではない。p95 は分布の形だけを示す |

---

## ライセンス

MIT — [LICENSE](LICENSE) を参照。
