from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass
from math import sqrt
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


@dataclass
class LocalVectorSearchClient:
    """Minimal vector-style retrieval PoC using hashed token vectors."""

    root_dir: Path
    include_paths: tuple[str, ...]
    max_file_bytes: int = 256_000
    dimension: int = 256

    def search(self, query: str, *, limit: int = 3) -> list[SearchResult]:
        root = self.root_dir.resolve()
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []
        query_vector = _hashed_vector(query_tokens, self.dimension)
        if not _has_magnitude(query_vector):
            return []

        candidates: list[SearchResult] = []
        for file_path in _candidate_files(root=root, include_paths=self.include_paths):
            content = _read_file_safely(file_path, self.max_file_bytes)
            if not content:
                continue

            content_tokens = _tokenize(content)
            if not content_tokens:
                continue

            doc_vector = _hashed_vector(content_tokens, self.dimension)
            similarity = _cosine_similarity(query_vector, doc_vector)
            if similarity <= 0:
                continue

            snippet = _build_snippet(content, query_tokens)
            try:
                rel = str(file_path.resolve().relative_to(root))
            except ValueError:
                rel = str(file_path.name)
            candidates.append(
                SearchResult(
                    path=rel,
                    snippet=snippet,
                    score=int(similarity * 1000),
                )
            )

        candidates.sort(key=lambda x: x.score, reverse=True)
        return candidates[:limit]


@dataclass
class HybridDocsSearchClient:
    keyword_client: LocalMarkdownSearchClient
    vector_client: LocalVectorSearchClient

    def search(self, query: str, *, limit: int = 3) -> list[SearchResult]:
        keyword_results = self.keyword_client.search(query, limit=max(limit, 6))
        vector_results = self.vector_client.search(query, limit=max(limit, 6))

        merged: dict[str, SearchResult] = {}
        for result in vector_results:
            merged[result.path] = SearchResult(path=result.path, snippet=result.snippet, score=result.score)

        for result in keyword_results:
            if result.path in merged:
                existing = merged[result.path]
                # keyword consistency is favored, vector score works as semantic tie-breaker.
                existing.score += result.score * 100
                if len(existing.snippet) < len(result.snippet):
                    existing.snippet = result.snippet
            else:
                merged[result.path] = SearchResult(
                    path=result.path,
                    snippet=result.snippet,
                    score=result.score * 100,
                )

        results = list(merged.values())
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:limit]


def build_default_search_client() -> DocsSearchClient:
    base = _detect_search_root(Path(__file__).resolve())
    include_raw = os.getenv("DOC_SEARCH_PATHS", "README.md,docs")
    include_paths = tuple(p.strip() for p in include_raw.split(",") if p.strip())
    if not include_paths:
        return NullDocsSearchClient()
    backend = os.getenv("DOC_SEARCH_BACKEND", "keyword").strip().lower()
    if backend == "vector":
        return LocalVectorSearchClient(root_dir=base, include_paths=include_paths)
    if backend == "hybrid":
        return HybridDocsSearchClient(
            keyword_client=LocalMarkdownSearchClient(root_dir=base, include_paths=include_paths),
            vector_client=LocalVectorSearchClient(root_dir=base, include_paths=include_paths),
        )
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


def _candidate_files(*, root: Path, include_paths: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for rel in include_paths:
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


def _detect_search_root(start: Path) -> Path:
    env_root = os.getenv("DOC_SEARCH_ROOT", "").strip()
    if env_root:
        return Path(env_root).resolve()

    for parent in start.parents:
        if (parent / "README.md").exists() and (parent / "docs").exists():
            return parent.resolve()
    return start.parents[0].resolve()


def _hashed_vector(tokens: list[str], dimension: int) -> list[float]:
    vector = [0.0] * max(8, dimension)
    expanded_tokens: list[str] = []
    for token in tokens:
        expanded_tokens.extend(_expand_token_features(token))
    counts = Counter(expanded_tokens)
    for token, count in counts.items():
        idx = hash(token) % len(vector)
        vector[idx] += float(count)
    return vector


def _has_magnitude(vector: list[float]) -> bool:
    return any(value != 0.0 for value in vector)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    if dot <= 0:
        return 0.0
    norm_a = sqrt(sum(x * x for x in a))
    norm_b = sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _expand_token_features(token: str) -> list[str]:
    features = [token]
    if len(token) < 2:
        return features
    max_n = min(4, len(token))
    for n in range(2, max_n + 1):
        for idx in range(0, len(token) - n + 1):
            features.append(token[idx : idx + n])
    return features
