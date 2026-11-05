from __future__ import annotations

import hmac
import json
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4

from app.service import AgentService, InMemoryConversationStore
from gateway import inbound
from policy.executor import IntentOrigin, ToolExecutor, ToolIntent, WriteNotSupportedError
from tools.base import InputSchema, PlannedExecution, ToolKind
from tools.conversation import ConversationSummarizeTool
from tools.docs_search import DocsSearchTool, SearchResult
from tools.registry import ToolRegistry

SIGNING_SECRET = "test-secret"


def _signed_headers(raw_body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    base = f"v0:{timestamp}:".encode("utf-8") + raw_body
    signature = "v0=" + hmac.new(SIGNING_SECRET.encode("utf-8"), base, "sha256").hexdigest()
    return {"X-Slack-Request-Timestamp": timestamp, "X-Slack-Signature": signature}


class FakeSearchClient:
    def __init__(self, results: list[SearchResult]) -> None:
        self._results = results
        self.queries: list[str] = []

    def search(self, query: str, *, limit: int = 3) -> list[SearchResult]:
        self.queries.append(query)
        return self._results[:limit]


class EchoLLMClient:
    """受け取ったプロンプトをそのまま返す。返信を見れば、LLM に何が渡ったかが分かる。"""

    def answer(self, question: str) -> str:
        return f"LLM<{question}>"


class RecordingToolExecutor(ToolExecutor):
    def __init__(self, registry: ToolRegistry) -> None:
        super().__init__(registry)
        self.handled: list[tuple[str, IntentOrigin]] = []

    def handle(self, intent: ToolIntent, *, origin: IntentOrigin):  # type: ignore[override]
        self.handled.append((intent.tool, origin))
        return super().handle(intent, origin=origin)


class ReadToolsThroughGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.search_client = FakeSearchClient(
            [SearchResult(path="docs/example.md", snippet="署名検証を実装する", score=3)]
        )
        conversation_store = InMemoryConversationStore()
        self.executor = RecordingToolExecutor(
            ToolRegistry(
                [
                    DocsSearchTool(self.search_client),
                    ConversationSummarizeTool(conversation_store),
                ]
            )
        )
        service = AgentService(
            llm_client=EchoLLMClient(),
            conversation_store=conversation_store,
            tool_executor=self.executor,
        )
        patcher = patch.object(inbound, "service", service)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _slash_command(self, command: str, text: str) -> dict:
        form = {
            "team_id": "T1",
            "channel_id": "C1",
            "user_id": "U1",
            "command": command,
            "text": text,
            "trigger_id": f"trig-{uuid4()}",
        }
        raw_body = urlencode(form).encode("utf-8")
        return inbound.handle_slack_command(
            form,
            headers=_signed_headers(raw_body),
            raw_body=raw_body,
            signing_secret=SIGNING_SECRET,
        )

    def test_ask_answers_from_docs_search_without_approval(self) -> None:
        response = self._slash_command("/ask", "署名検証って何？")

        self.assertEqual(response["status"], "completed")
        self.assertNotIn("blocks", response)
        self.assertEqual(response["metadata"]["sources"], ["docs/example.md"])
        self.assertIn("質問: 署名検証って何？", response["text"])
        self.assertIn("source=docs/example.md", response["text"])
        self.assertEqual(self.search_client.queries, ["署名検証って何？"])
        self.assertEqual(self.executor.handled, [("docs.search", IntentOrigin.REQUESTER)])

    def _slack_event(self, text: str, *, thread_ts: str) -> dict:
        payload = {
            "event_id": f"evt-{uuid4()}",
            "team_id": "T1",
            "event": {
                "ts": f"{time.time():.6f}",
                "thread_ts": thread_ts,
                "channel": "C1",
                "user": "U1",
                "text": text,
            },
        }
        raw_body = json.dumps(payload).encode("utf-8")
        return inbound.handle_slack_event(
            payload,
            headers=_signed_headers(raw_body),
            raw_body=raw_body,
            signing_secret=SIGNING_SECRET,
        )

    def test_summarize_reads_the_thread_without_approval(self) -> None:
        thread_ts = f"{time.time():.6f}"
        self._slack_event("明日の会議は10時から", thread_ts=thread_ts)
        self._slack_event("資料は共有フォルダにあります", thread_ts=thread_ts)
        self.executor.handled.clear()

        response = self._slack_event("/summarize current_thread", thread_ts=thread_ts)

        self.assertEqual(response["status"], "completed")
        self.assertNotIn("blocks", response)
        self.assertEqual(response["metadata"]["message_count"], 2)
        self.assertIn("1. U1: 明日の会議は10時から", response["text"])
        self.assertIn("2. U1: 資料は共有フォルダにあります", response["text"])
        self.assertEqual(self.executor.handled, [("conversation.summarize", IntentOrigin.REQUESTER)])

    def test_summarize_reports_when_the_thread_is_empty(self) -> None:
        response = self._slack_event("/summarize current_thread", thread_ts=f"{time.time():.6f}")

        self.assertEqual(response["status"], "not_found")
        self.assertEqual(response["text"], "要約対象メッセージが見つかりませんでした。")
        self.assertEqual(response["metadata"]["message_count"], 0)


class FakeWriteTool:
    name = "fake.write"
    kind = ToolKind.WRITE
    input_schema = InputSchema()

    def __init__(self) -> None:
        self.executed = 0

    def dry_run(self, tool_input):
        return PlannedExecution(tool=self.name, input=dict(tool_input), description="書き込む")

    def execute(self, tool_input, *, idempotency_key: str):
        self.executed += 1
        return {}


class ToolExecutorEntryTests(unittest.TestCase):
    def test_write_tool_is_never_executed_without_approval(self) -> None:
        tool = FakeWriteTool()
        executor = ToolExecutor(ToolRegistry([tool]))

        with self.assertRaises(WriteNotSupportedError):
            executor.handle(ToolIntent(tool="fake.write", input={}), origin=IntentOrigin.REQUESTER)

        self.assertEqual(tool.executed, 0)


if __name__ == "__main__":
    unittest.main()
