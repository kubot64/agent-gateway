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
| `policy` | 承認待ちと、依頼者本人による承認（今は空） |
| `tools` | Linear クライアント、docs 検索 |
| `infra` | DB と Redis の store、組み立て |

層の契約は import-linter で検査する（設定は `backend/pyproject.toml`）。infra は契約の対象外で、組み立てのために `app` を import する。

今の契約は、上の層が下の層を飛び越えて import することを許す。そのため `app` は `tools` を直接呼んでいる。これは ADR-0003 の「ツールを登録簿から取り出して実行する経路は policy 層だけが持つ」とまだ合っていない。policy 層を実装するときに、この経路を締める。

```bash
cd backend && PYTHONPATH=src lint-imports
```

## test

```bash
cd backend && PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py' -v
```

## run api

```bash
cd backend && PYTHONPATH=src uvicorn gateway.main:app --host 0.0.0.0 --port 8000
```

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

docker compose でも直接入口は既定で無効だ。使うときは `ENABLE_DIRECT_HANDLE_MESSAGE=1 docker compose --profile app up -d --build` のように渡す。E2E（`backend/scripts/e2e_pg_redis.sh`）は自分でこの変数を立てる。

本格的な README（解決する問題、構成図、4 つの安全、ADR）は 2026-11 中に書き換えます。
