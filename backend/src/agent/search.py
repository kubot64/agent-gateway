from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class SearchResult:
    path: str
    snippet: str
    score: int


class DocsSearchClient(Protocol):
    def search(self, query: str, *, limit: int = 3) -> list[SearchResult]:
        ...


@dataclass
class NullDocsSearchClient:
    def search(self, query: str, *, limit: int = 3) -> list[SearchResult]:
        return []


@dataclass
class LocalMarkdownSearchClient:
    root_dir: Path
    include_paths: tuple[str, ...]
    max_file_bytes: int = 256_000

    def search(self, query: str, *, limit: int = 3) -> list[SearchResult]:
        root = self.root_dir.resolve()
        tokens = _tokenize(query)
        if not tokens:
            return []

        candidates: list[SearchResult] = []
        for file_path in self._candidate_files():
            content = _read_file_safely(file_path, self.max_file_bytes)
            if not content:
                continue

            score = _score(content, tokens)
            if score <= 0:
                continue
            snippet = _build_snippet(content, tokens)
            try:
                rel = str(file_path.resolve().relative_to(root))
            except ValueError:
                rel = str(file_path.name)
            candidates.append(SearchResult(path=rel, snippet=snippet, score=score))

        candidates.sort(key=lambda x: x.score, reverse=True)
        return candidates[:limit]

    def _candidate_files(self) -> list[Path]:
        root = self.root_dir.resolve()
        files: list[Path] = []
        for rel in self.include_paths:
            target = (root / rel).resolve()
            if not str(target).startswith(str(root)):
                continue
            if not target.exists():
                continue
            if target.is_file() and target.suffix.lower() in {".md", ".markdown"}:
                files.append(target)
                continue
            if target.is_dir():
                for child in target.rglob("*"):
                    if child.is_file() and child.suffix.lower() in {".md", ".markdown"}:
                        files.append(child)
        return files


def build_default_search_client() -> DocsSearchClient:
    base = Path(__file__).resolve().parents[3]
    include_raw = os.getenv("DOC_SEARCH_PATHS", "README.md,docs,games,travel")
    include_paths = tuple(p.strip() for p in include_raw.split(",") if p.strip())
    if not include_paths:
        return NullDocsSearchClient()
    return LocalMarkdownSearchClient(root_dir=base, include_paths=include_paths)


_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[一-龯ぁ-んァ-ンー]+")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_PATTERN.findall(text)]


def _read_file_safely(path: Path, max_file_bytes: int) -> str:
    try:
        if path.stat().st_size > max_file_bytes:
            return ""
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _score(content: str, tokens: list[str]) -> int:
    lowered = content.lower()
    return sum(lowered.count(token) for token in tokens)


def _build_snippet(content: str, tokens: list[str], max_len: int = 140) -> str:
    lowered = content.lower()
    first_pos = -1
    for token in tokens:
        idx = lowered.find(token)
        if idx >= 0 and (first_pos == -1 or idx < first_pos):
            first_pos = idx

    if first_pos < 0:
        return content[:max_len].replace("\n", " ").strip()

    start = max(0, first_pos - 30)
    end = min(len(content), first_pos + max_len)
    return content[start:end].replace("\n", " ").strip()
