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

## run with postgres + redis

```bash
cd infra && docker compose --profile app up -d --build
```

本格的な README（解決する問題、構成図、4 つの安全、ADR）は 2026-11 中に書き換えます。
