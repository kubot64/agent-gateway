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

```bash
cd backend && PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py' -v
```

## run api

```bash
cd backend && PYTHONPATH=src uvicorn gateway.main:app --host 0.0.0.0 --port 8000
```

## run with postgres + redis

```bash
cd infra && docker compose --profile app up -d --build
```

本格的な README（解決する問題、構成図、4 つの安全、ADR）は 2026-11 中に書き換えます。
