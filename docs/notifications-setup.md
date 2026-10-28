# スケジュール通達 実運用セットアップ手順

`POST /v1/notify/scheduled` に任意テキストを送ると、Teams と Chatwork の両方へ同報する。
外部スケジューラ（Cloud Scheduler / cron 等）が定期的にこのエンドポイントを叩く運用を想定。

> **未設定チャネルは自動で no-op**。Teams だけ／Chatwork だけの設定でも動く。
> 片方の配信が失敗しても、もう片方は配信される（部分失敗を隔離）。

関連コード: [`core/notifications.py`](../core/notifications.py) / エンドポイント: [`service/api.py`](../service/api.py)

---

## 全体の流れ

1. Teams の Webhook URL を取得
2. Chatwork の API トークンとルームID を取得
3. （ローカル）`.env` に設定して動作確認
4. （本番）Secret Manager に登録 → Cloud Run にデプロイ
5. Cloud Scheduler で定期実行を設定

> ⚠️ **本番デプロイ（手順4・5の実行）は担当分担上ここでは行わない**。コマンドは用意するので、
> 実行はデプロイ担当（人間 / Cursor）が行うこと。

---

## 1. Teams Webhook URL の取得

通知を出したい **チャンネル** で Webhook 受け口の URL を発行する。作り方は2通り。

### ① 従来型 Incoming Webhook（本実装が対応済み）

1. チャンネル名の右「⋯」→ **[コネクタ]**（または [コネクタを管理]）
2. **[Incoming Webhook]** を検索して [構成] / [追加]
3. 名前（例: `AI基盤通達`）を付けて [作成]
4. 表示された **URL をコピー** → これが `TEAMS_WEBHOOK_URL`

本実装はこの形式（`{"text": "..."}` を POST）に対応している。

### ② 新型 Workflows（Power Automate）を使う場合

[コネクタ] が見当たらず [ワークフロー] からしか作れない環境では、ペイロード形式（Adaptive Card）が
異なるため **`TeamsNotifier._build_request` の改修が必要**。その場合は開発担当に依頼すること。

### 動作確認（任意）

```bash
curl -X POST "<コピーしたURL>" \
  -H "Content-Type: application/json" \
  -d '{"text":"テスト通知です"}'
```

チャンネルに「テスト通知です」が出れば OK。

---

## 2. Chatwork API トークン & ルームID の取得

### API トークン

1. Chatwork にログイン → 右上の自分のアイコン → **[サービス連携]**
2. 左メニュー **[API Token]** → パスワード入力 → 表示されたトークンをコピー
   - これが `CHATWORK_API_TOKEN`

### ルームID

投稿したいグループチャット（ルーム）を開いたときの URL 末尾の数字。

```
https://www.chatwork.com/#!rid123456789
                                └────────┘ ← この 123456789 が CHATWORK_ROOM_ID
```

---

## 3. ローカルで動作確認

```bash
cp .env.example .env      # 未作成なら
```

`.env` に以下を設定（`AI_PLATFORM_API_KEYS` は既に設定済みの想定）:

```dotenv
TEAMS_WEBHOOK_URL=https://outlook.office.com/webhook/xxxxxxxx
CHATWORK_API_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
CHATWORK_ROOM_ID=123456789
```

起動して叩く:

```bash
uvicorn service.api:app --reload
```

```bash
curl -X POST http://localhost:8000/v1/notify/scheduled \
  -H "X-API-Key: <あなたのAPIキー>" -H "Content-Type: application/json" \
  -d '{"message":"ローカルからの通達テストです"}'
```

レスポンス例（両チャネル成功）:

```json
{
  "delivered": true,
  "configured_channels": 2,
  "results": [
    {"channel": "teams", "ok": true, "skipped": false, "status_code": 200, "error": null},
    {"channel": "chatwork", "ok": true, "skipped": false, "status_code": 200, "error": null}
  ],
  "request_id": "..."
}
```

| HTTP | 意味 |
|------|------|
| 200 `delivered:true` | 設定済みチャネル全てに配信成功 |
| 200 `delivered:false` `configured_channels:0` | 送信先が未設定（no-op）。env を確認 |
| 502 | 一部/全部の配信に失敗（`results` の `error` を確認） |
| 401 | `X-API-Key` が無効 |
| 422 | `message` が空 |

---

## 4. 本番（Cloud Run）へ反映 ※デプロイ担当が実行

Webhook URL とトークンは **Secret Manager** に登録し、[`infra/deploy.yaml`](../infra/deploy.yaml) の
`secretKeyRef` から注入する（配線済み）。`CHATWORK_ROOM_ID` は manifest の平文値を差し替える。

```bash
# PROJECT / REGION は環境に合わせて置換
# Teams Webhook URL
printf '%s' "https://outlook.office.com/webhook/xxxx" \
  | gcloud secrets create teams-webhook-url --project=PROJECT --data-file=-
# Chatwork API トークン
printf '%s' "xxxxxxxxxxxxxxxxxxxx" \
  | gcloud secrets create chatwork-api-token --project=PROJECT --data-file=-

# Cloud Run 実行 SA に Secret 参照権限を付与（初回のみ）
for S in teams-webhook-url chatwork-api-token; do
  gcloud secrets add-iam-policy-binding "$S" --project=PROJECT \
    --member="serviceAccount:RUN_SERVICE_ACCOUNT" \
    --role="roles/secretmanager.secretAccessor"
done
```

`infra/deploy.yaml` の `REPLACE_WITH_ROOM_ID` を実際のルームIDに変更してからデプロイする。

---

## 5. Cloud Scheduler で定期実行

エンドポイントは `X-API-Key` を要求するので、ジョブのヘッダに API キーを載せる。

```bash
# 例: 平日 9:00(JST)に定例通達を送る
gcloud scheduler jobs create http notify-scheduled-weekday-0900 \
  --project=PROJECT \
  --location=REGION \
  --schedule="0 9 * * 1-5" \
  --time-zone="Asia/Tokyo" \
  --uri="https://<CloudRunのURL>/v1/notify/scheduled" \
  --http-method=POST \
  --headers="Content-Type=application/json,X-API-Key=<あなたのAPIキー>" \
  --message-body='{"message":"本日9:00の定例通達です。"}'
```

- スケジュール書式は cron（`分 時 日 月 曜日`）。`0 9 * * 1-5` = 平日9時。
- 文面を変えたい / 複数の時刻に送りたい場合は、ジョブを複数作る（`--message-body` を変える）。
- **手動テスト実行**: `gcloud scheduler jobs run notify-scheduled-weekday-0900 --location=REGION --project=PROJECT`

### （任意）多層防御: Cloud Run 側も認証必須にする

本アプリは自前で `X-API-Key` 認証を行うが、Cloud Run を「認証必須」にして Scheduler から
OIDC で叩くとより安全。その場合は上記に加えて:

```bash
  --oidc-service-account-email="SCHEDULER_SA@PROJECT.iam.gserviceaccount.com" \
  --oidc-token-audience="https://<CloudRunのURL>"
```

を付与し、Cloud Run 側で当該 SA に `roles/run.invoker` を付ける。

---

## トラブルシュート

| 症状 | 原因 / 対処 |
|------|------|
| `configured_channels: 0` | env が未注入。Cloud Run のリビジョンに Secret が反映されているか確認 |
| Chatwork だけ届かない | `CHATWORK_API_TOKEN` / `CHATWORK_ROOM_ID` の両方が必要。片方だけだと chatwork は無効（ログに警告） |
| `502` かつ `error` に `403` | Teams: Webhook が削除/失効。Chatwork: トークン権限やルームID誤り |
| Teams だけ届かない | Webhook が新型(Workflows)の可能性。手順1②を参照 |
