from __future__ import annotations

from policy.executor import ToolExecutor
from tools.conversation import ConversationReader, ConversationSummarizeTool
from tools.docs_search import DocsSearchTool, build_default_search_client
from tools.registry import ToolRegistry


def build_tool_executor(*, conversations: ConversationReader) -> ToolExecutor:
    """既定のツールを登録した ToolExecutor を作る。"""
    registry = ToolRegistry(
        [
            DocsSearchTool(build_default_search_client()),
            ConversationSummarizeTool(conversations),
        ]
    )
    return ToolExecutor(registry)
