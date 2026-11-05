# agent-gateway

Safely bridge LLM agents to Slack / Discord: signed inbound, idempotency, approval gates, provider fallback.

Slack / Discord からの指示を、署名検証・冪等性・承認フロー・プロバイダ切替を通して
LLM エージェントに安全に渡すための基盤です。2026-11 に `home` リポジトリから切り出しました。

- 計画と判定条件: [docs/agent_gateway_plan.md](docs/agent_gateway_plan.md)
- 設計書: [docs/slack_discord_secretary_design.md](docs/slack_discord_secretary_design.md)
- Cloud Run / Slack / Discord の構築手順: [docs/cloudrun_slack_discord_setup.md](docs/cloudrun_slack_discord_setup.md)

## test

```bash
cd backend && PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py' -v
```

## run api

```bash
cd backend && PYTHONPATH=src uvicorn api.app:app --host 0.0.0.0 --port 8000
```

## run with postgres + redis

```bash
cd infra && docker compose --profile app up -d --build
```

本格的な README（解決する問題、構成図、4 つの安全、ADR）は 2026-11 中に書き換えます。
