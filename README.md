# agent-gateway

Safely bridge LLM agents to Slack / Discord: signed inbound, idempotency, approval gates, provider fallback.

Slack / Discord からの指示を、署名検証・冪等性・承認フロー・プロバイダ切替を通して
LLM エージェントに安全に渡すための基盤です。2026-11 に `home` リポジトリから切り出しました。

- 計画と判定条件: [docs/agent_gateway_plan.md](docs/agent_gateway_plan.md)
- 設計書: [docs/slack_discord_secretary_design.md](docs/slack_discord_secretary_design.md)
- Cloud Run / Slack / Discord の構築手順: [docs/cloudrun_slack_discord_setup.md](docs/cloudrun_slack_discord_setup.md)

## 構成

`backend/src` は 4 層と、層の外にある infra に分かれる。依存は上から下への一方向だけで、下の層は上の層を import しない。

| 層 | 受け持つもの |
|---|---|
| `gateway` | HTTP の入口、署名検証、受信の冪等性、Slack / Discord の入出力 |
| `app` | AgentService、意図分類（parser）、LLM 呼び出し |
| `policy` | 意図を受け取ってツールを実行する入口（`ToolExecutor`）。読み取りはそのまま実行する。承認待ちと、依頼者本人による承認はこれから足す |
| `tools` | ツールの I/F、登録簿、ツール（`docs.search`、`conversation.summarize`）、Linear クライアント |
| `infra` | DB と Redis の store、組み立て |

層の契約は import-linter で検査する（設定は `backend/pyproject.toml`）。infra は契約の対象外で、組み立てのために `app` を import する。

契約は 2 つある。

- **layers**: gateway → app → policy → tools の順で、上向きの import を禁じる。
- **forbidden**: gateway と app から tools への直接 import を禁じる。ツールを登録簿から取り出して実行できるのは policy だけにするためだ（ADR-0003）。例外は `app.service -> tools.linear` だけで、`linear.create_issue` をツールにする #12 で外す。

```bash
cd backend && PYTHONPATH=src lint-imports
```

## test

Python 3.11 以上が要る。依存は `backend/pyproject.toml` にまとめてあり、CI も同じ定義から入れる。

```bash
cd backend && python3 -m pip install -e ".[dev]"
```

```bash
cd backend && PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py' -v
```

lint（ruff）と型チェック（mypy）は次のとおり。設定は `backend/pyproject.toml` にある。

```bash
cd backend && ruff check .
```

```bash
cd backend && mypy
```

## run api

```bash
cd backend && PYTHONPATH=src uvicorn gateway.main:app --host 0.0.0.0 --port 8000
```

### 署名鍵

Slack と Discord の入口は、署名鍵が設定されていないと 401 を返す。鍵がないまま署名検証を飛ばすと、誰でも依頼者を名乗ったり承認したりできるからだ。

| 環境変数 | 必要な入口 |
|---|---|
| `SLACK_SIGNING_SECRET` | `/api/inbound/slack/events`、`/api/inbound/slack/commands`、`/api/inbound/slack/interactions` |
| `DISCORD_PUBLIC_KEY` | `/api/inbound/discord/interactions` |

ローカルで Slack の入口を叩くときも、鍵を設定し、その鍵で署名したリクエストを送る。署名の作り方は `backend/scripts/e2e_pg_redis.sh` の `post_slack_signed` を参照。

### 直接入口 `/api/agent/handle-message`

`POST /api/agent/handle-message` は、Slack / Discord を通さずに依頼を送る開発用の入口だ。署名検証がなく、本文の `user_id` で誰にでもなりすませる。そのため既定では無効で、404 を返す。

ローカル開発で使うときだけ、環境変数 `ENABLE_DIRECT_HANDLE_MESSAGE=1` を立てて起動する（`true`、`yes`、`on` も有効とみなす）。本番には設定しない。

```bash
cd backend && ENABLE_DIRECT_HANDLE_MESSAGE=1 PYTHONPATH=src uvicorn gateway.main:app --host 127.0.0.1 --port 8000
```

```bash
curl -sS -X POST http://127.0.0.1:8000/api/agent/handle-message \
  -H "content-type: application/json" \
  -d '{"platform":"api","workspace_id":"W1","channel_id":"C1","user_id":"U1","text":"/ask こんにちは"}'
```

承認は署名検証済みの Slack interaction（`POST /api/inbound/slack/interactions`）からだけ受け付ける。以前あった認証なしの `POST /api/agent/confirm-action` は廃止した（ADR-0003）。

## run with postgres + redis

```bash
cd infra && docker compose --profile app up -d --build
```

docker compose でも直接入口は既定で無効で、署名鍵も入っていない。使うときは `ENABLE_DIRECT_HANDLE_MESSAGE=1 SLACK_SIGNING_SECRET=... docker compose --profile app up -d --build` のように実行環境から渡す。E2E（`backend/scripts/e2e_pg_redis.sh`）は E2E 用の値を自分で立て、終了時にテスト用アプリを止める。

本格的な README（解決する問題、構成図、4 つの安全、ADR）は 2026-11 中に書き換えます。
