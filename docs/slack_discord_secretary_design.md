# Slack / Discord 秘書エージェント 設計書（MVP→本番）

## 1. 目的
- Slack / Discord から自然文やコマンドで指示を送り、個人秘書のようにタスク実行・通知・要約を行う。
- 裏側LLM（OpenAI / Gemini / Claude）を切り替え可能にし、コスト・品質・可用性を最適化する。

## 2. スコープ
### MVP（2〜4週間）
- スレッド要約
- タスク登録（1つの外部サービス連携: Notion or Linear）
- リマインド作成
- Q&A（社内/個人ナレッジのRAG検索）
- 危険操作の確認（Confirm / Cancel）

### 非スコープ（MVP外）
- 完全自律実行（承認なしの連続操作）
- 複雑な複数人ワークフロー管理
- 高度な音声UI

## 3. 全体アーキテクチャ
```text
[Slack Bot] ----\
                 > [Inbound Gateway] -> [Orchestrator API] -> [LLM Router] -> [LLM Provider Adapters]
[Discord Bot] --/                            |                     |             (OpenAI/Gemini/Claude)
                                              |                     v
                                              |               [Tool Executor] -> Calendar / Notion / Search / DB
                                              |
                                              +-> [Task Queue/Worker] (long jobs, reminders)
                                              +-> [PostgreSQL] (users, threads, audit, tasks)
                                              +-> [Vector DB] (RAG)
                                              +-> [Observability] (logs, traces, metrics)
```

## 4. 主要コンポーネント

### 4.1 Inbound Gateway
責務:
- Slack Events API / Discord Interaction を受信
- 署名検証（Slack signing secret, Discord signature）
- 冪等性キーで重複イベント除去
- 共通イベント形式へ正規化

共通イベント例:
```json
{
  "platform": "slack",
  "workspace_id": "T123",
  "channel_id": "C123",
  "thread_id": "1710000000.000100",
  "user_id": "U123",
  "message_id": "1710000001.000200",
  "text": "明日の10時に◯◯をリマインド",
  "ts": "2026-02-22T10:00:00Z"
}
```

### 4.2 Orchestrator API
責務:
- 意図分類（ask / summarize / remind / task / admin）
- 実行計画作成（Plan）
- ツール呼び出し順序制御
- 応答整形（短文/詳細、口調、言語）

状態遷移:
1. RECEIVED
2. CLASSIFIED
3. NEED_CONFIRMATION（危険操作時）
4. EXECUTING
5. COMPLETED / FAILED

### 4.3 LLM Router + Provider Adapter
責務:
- 要件に応じたモデル選択
- モデル障害時フォールバック
- API差分吸収（tool calling schema差分）

選択ルール（初期）:
- 低遅延優先: 軽量モデル
- 長文要約: 長コンテキスト得意モデル
- 高難度推論: 高性能モデル
- 失敗時: 別プロバイダへ1回自動リトライ

### 4.4 Tool Executor
共通I/F:
- `name`
- `input_schema`
- `execute(input, auth_context)`
- `idempotency_key`
- `dry_run`

初期ツール:
- reminder.create
- tasks.create
- docs.search
- thread.summarize

安全制御:
- ツールごとに権限ポリシー
- タイムアウト
- 監査ログ（input/output hash + actor）

### 4.5 Task Queue / Worker
用途:
- リマインド予約
- 長時間リサーチ
- バッチ要約

推奨:
- Redis Queue または SQS + Worker
- 失敗時 retry with backoff

### 4.6 Data Stores
PostgreSQL テーブル（最小）:
- users
- channel_bindings
- conversations
- conversation_summaries
- tasks
- reminders
- audit_logs
- tool_executions

Vector DB:
- 個人メモ・FAQ・ドキュメント断片
- メタデータ（source, owner, permission）必須

## 5. コマンド / UX設計

### Slack / Discord 両対応コマンド
- `/ask <質問>`
- `/summarize [このスレッド]`
- `/task add <内容> [期限]`
- `/remind <日時> <内容>`
- `/mode brief|detailed`

### 自然文対応
- 「明日9時に資料提出をリマインドして」
- 「このスレッド3行でまとめて」

### 危険操作の確認UI
- 例: 一斉通知、削除、外部公開
- UI: Confirm / Cancel ボタン
- TTL: 5分以内に応答なければ失効

## 6. セキュリティ / ガバナンス
- 署名検証（必須）
- RBAC（user_id -> role -> action）
- PIIマスキング（ログ保存時）
- 監査証跡（誰が何を指示し何が実行されたか）
- データ保持ポリシー（例: 90日）

禁止事項（初期）:
- 承認なし外部送信
- 承認なし削除操作
- 未許可コネクタへの接続

## 7. 可観測性（運用で必須）
メトリクス:
- p50/p95 latency
- tool success rate
- fallback率
- hallucination疑い率（手動ラベルでも可）
- cost / command

ログ:
- request_id, conversation_id, platform, model, tool, duration_ms, status

## 8. API設計（最小）

### Inbound
- `POST /api/inbound/slack/events`
- `POST /api/inbound/discord/interactions`

### Agent
- `POST /api/agent/handle-message`
- `POST /api/agent/confirm-action`

### Internal
- `POST /api/internal/tools/execute`
- `POST /api/internal/reminders/fire`

## 9. ディレクトリ構成（実装例）
```text
backend/
  src/
    adapters/
      slack/
      discord/
      llm/
        openai.ts
        gemini.ts
        claude.ts
    orchestrator/
      classifier.ts
      planner.ts
      responder.ts
    tools/
      reminder-create.ts
      task-create.ts
      docs-search.ts
    workers/
      reminders-worker.ts
    db/
      schema.sql
    api/
      inbound.ts
      agent.ts
  tests/
    e2e/
    unit/
infra/
  docker-compose.yml
  terraform/
docs/
  slack_discord_secretary_design.md
```

## 10. 実装ロードマップ

### Phase 1（MVP）
- Slack受信
- `/ask`, `/summarize`, `/remind`
- OpenAI 1プロバイダのみ
- PostgreSQL + Redis

完了条件:
- 50ケースの回帰テストで 90%以上成功
- p95 6秒以内（短文）

### Phase 2
- Discord追加
- Gemini/Claude adapter追加
- Router + fallback
- Notion/Linear連携

### Phase 3
- 権限高度化
- 管理画面（監査・設定）
- 評価基盤の自動化（A/B）

## 11. 失敗パターンと対策
- 問題: 命令解釈ミス
  - 対策: コマンド優先 + 不明時確認質問
- 問題: ツール暴走
  - 対策: dry-run + confirmation gate
- 問題: コスト急増
  - 対策: ルータで上限設定 + 長文要約の段階実行

## 12. まず作るべき具体タスク（今週）
1. Slack app作成（Events + Slash Commands）
2. Inbound Gatewayで署名検証実装
3. `/ask` を単一LLMで接続
4. `reminder.create` ツール作成
5. Confirm/Cancel UI実装
6. audit_logs へ保存
7. 基本ダッシュボード（latency/error/cost）

---
この設計をベースに、次は「使う技術スタック（TypeScript or Python）」を固定して、API契約とDBスキーマを実コード化する。

## 13. 実装ステータス（2026-02-22）
実装基準日: 2026-02-22

### 実装済み
- Slack署名検証（HMAC SHA256 / 5分許容）
- Slack inbound API
  - `POST /api/inbound/slack/events`
  - `POST /api/inbound/slack/commands`
  - `POST /api/inbound/slack/interactions`
  - 重複イベント排除（`event_id` / message key）
- Slack outbound API
  - `chat.postMessage`（`SLACK_BOT_TOKEN` 設定時、message eventに自動返信）
- Discord inbound API
  - `POST /api/inbound/discord/interactions`（ping / command）
  - Ed25519 署名検証（`X-Signature-Ed25519` / `X-Signature-Timestamp`）
  - 重複イベント排除（interaction id）
- Agent API
  - `POST /api/agent/confirm-action`
  - `GET /api/health`
  - `GET /api/metrics`
- `/ask` のLLM接続（OpenAI + ローカルフォールバック）
- `/task add` のLinear連携（`LINEAR_API_KEY`, `LINEAR_TEAM_ID`）
- `reminder.create` 永続化（SQLite）
- Confirm/Cancel TTL（5分）
- 監査ログ保存（SQLite `audit_logs`）
- Confirm/Cancel Block Kit
  - `action_id=confirm|cancel`
  - `value=confirmation_id`

### 未実装
- RAG検索
- 本番向け可観測性（トレース/メトリクス外部連携）
- PostgreSQL / Redis への移行

### 実装との差分メモ
- 設計上は PostgreSQL + Redis を想定しているが、現実装は SQLite 単体。
- `POST /api/agent/handle-message` は未公開。現状は Slack inbound 経由で処理。
