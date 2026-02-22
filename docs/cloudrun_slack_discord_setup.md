# Cloud Run + Slack/Discord Setup

この手順で、`/Users/morikubo-takashi/Codex/home` のAPIを Cloud Run にデプロイし、Slack/Discord から呼び出せるようにする。

## 1. 前提
- `gcloud` が利用可能
- GCP Project 作成済み
- Slack App / Discord Application 作成済み

## 2. 必須シークレット作成
Cloud Run deploy スクリプトは以下の Secret を参照する。

- `postgres-dsn`
- `slack-signing-secret`
- `slack-bot-token`
- `discord-public-key`
- （任意）`openai-api-key`

例:

```bash
gcloud secrets create postgres-dsn --replication-policy=automatic
echo -n 'postgresql://USER:PASSWORD@HOST:5432/DBNAME' | gcloud secrets versions add postgres-dsn --data-file=-

gcloud secrets create slack-signing-secret --replication-policy=automatic
echo -n 'xxx' | gcloud secrets versions add slack-signing-secret --data-file=-

gcloud secrets create slack-bot-token --replication-policy=automatic
echo -n 'xoxb-...' | gcloud secrets versions add slack-bot-token --data-file=-

gcloud secrets create discord-public-key --replication-policy=automatic
echo -n '...' | gcloud secrets versions add discord-public-key --data-file=-
```

## 3. Cloud Run deploy

```bash
cd /Users/morikubo-takashi/Codex/home

GCP_PROJECT=YOUR_PROJECT_ID \
GCP_REGION=asia-northeast1 \
SERVICE_NAME=home-agent-api \
AR_REPO=home \
DATABASE_BACKEND=postgres \
IDEMPOTENCY_BACKEND=postgres \
DOC_SEARCH_BACKEND=keyword \
infra/cloudrun/deploy.sh
```

デプロイ後に以下URLが出力される。
- Slack Events: `.../api/inbound/slack/events`
- Slack Commands: `.../api/inbound/slack/commands`
- Slack Interactions: `.../api/inbound/slack/interactions`
- Discord Interactions: `.../api/inbound/discord/interactions`

## 4. Slack設定

### 4.1 App Manifest
Cloud Run URLでmanifestをレンダリング:

```bash
cd /Users/morikubo-takashi/Codex/home/infra/slack
CLOUD_RUN_URL=https://YOUR_SERVICE_URL ./render_manifest.sh > app-manifest.rendered.yaml
```

`app-manifest.rendered.yaml` を Slack App 管理画面で Import する。

### 4.2 署名シークレット
Slack App 管理画面の Signing Secret を `slack-signing-secret` としてSecret Managerへ格納済みであることを確認。

## 5. Discord設定

### 5.1 Interactions Endpoint URL
Discord Developer Portal で
`https://YOUR_SERVICE_URL/api/inbound/discord/interactions` を設定する。

### 5.2 Slash Commands登録

```bash
cd /Users/morikubo-takashi/Codex/home/infra/discord
DISCORD_APP_ID=YOUR_APP_ID \
DISCORD_BOT_TOKEN=YOUR_BOT_TOKEN \
./register_commands.sh
```

## 6. 動作確認

### API health
```bash
curl -sS https://YOUR_SERVICE_URL/api/health
```

### Slack slash command
- Slack で `/ask 今日の予定は？` を実行

### Discord slash command
- Discord で `/ask question: 今日の予定は？` を実行

## 7. 運用メモ
- Cloud Run は webhook 受信のため通常 `--allow-unauthenticated` が必要。
- 機密情報は Secret Manager で管理し、平文ENVは避ける。
- Slack/Discord側URL変更後は、必ず再疎通確認する。
