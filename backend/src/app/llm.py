from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Protocol
from urllib import error, request


class LLMClient(Protocol):
    def answer(self, question: str) -> str:
        """Return a short answer for the provided question."""


@dataclass
class LocalFallbackLLMClient:
    def answer(self, question: str) -> str:
        if not question:
            return "質問内容が空でした。"
        return f"（ローカル回答）質問を受け付けました: {question}"


@dataclass
class OpenAIChatLLMClient:
    api_key: str
    model: str = "gpt-4o-mini"
    timeout_seconds: int = 15

    def answer(self, question: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You are a concise personal assistant."},
                {"role": "user", "content": question},
            ],
            "temperature": 0.2,
        }
        req = request.Request(
            url="https://api.openai.com/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with request.urlopen(req, timeout=self.timeout_seconds) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        choices = body.get("choices", [])
        if not choices:
            return "回答を生成できませんでした。"
        message = choices[0].get("message", {})
        content = str(message.get("content", "")).strip()
        return content or "回答を生成できませんでした。"


def build_default_llm_client() -> LLMClient:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    if api_key:
        return SafeLLMClient(primary=OpenAIChatLLMClient(api_key=api_key, model=model), fallback=LocalFallbackLLMClient())
    return LocalFallbackLLMClient()


@dataclass
class SafeLLMClient:
    primary: LLMClient
    fallback: LLMClient

    def answer(self, question: str) -> str:
        try:
            return self.primary.answer(question)
        except (TimeoutError, OSError, error.URLError, error.HTTPError, ValueError, KeyError):
            return self.fallback.answer(question)
