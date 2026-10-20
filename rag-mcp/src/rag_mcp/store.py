# Author: 정유은
# Date: 20OCT2026
# Purpose: 문서 검색 저장소 계약(DocStore), BM25 키워드 점수, 파일 기반 메모리 저장소. pgvector 저장소는 pgvector_store.py.

"""문서 저장소.

- `DocStore`: rag-mcp 서버가 쓰는 계약. 메모리 저장소와 pgvector 저장소가 같은 계약을 따른다.
- `Bm25`: 키워드 점수 (한국어는 글자 2-gram). 메모리 저장소의 검색과 pgvector 저장소의 혼합 점수에 쓴다.
- `InMemoryKeywordStore`: DB 없이 파일만으로 동작하는 저장소. 테스트와 DB 없는 로컬 실행용.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import asdict
from typing import Any, Protocol

from .docs import Chunk, Doc, chunk_doc

Json = dict[str, Any]


class DocStore(Protocol):
    def search(self, query: str, top_k: int, doc_types: list[str] | None) -> list[Json]: ...
    def get_doc_raw(self, doc_id: str) -> str | None: ...
    def list_docs(self) -> list[Json]: ...
    def info(self) -> Json: ...


_WORD = re.compile(r"[0-9A-Za-z_]+|[가-힣]+")


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for word in _WORD.findall(text.lower()):
        if word[0].isascii():
            tokens.append(word)
        elif len(word) <= 2:
            tokens.append(word)
        else:
            tokens.extend(word[i : i + 2] for i in range(len(word) - 1))
    return tokens


class Bm25:
    k1 = 1.2
    b = 0.75

    def __init__(self, texts: list[str]) -> None:
        self._tf = [Counter(tokenize(t)) for t in texts]
        self._len = [sum(tf.values()) for tf in self._tf]
        self._avg_len = sum(self._len) / max(len(self._len), 1)
        df: Counter[str] = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        n = len(texts)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def score(self, i: int, query: list[str]) -> float:
        tf, length = self._tf[i], self._len[i]
        total = 0.0
        for t in set(query):
            if t not in tf:
                continue
            f = tf[t]
            total += self._idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * length / self._avg_len))
        return total


def chunk_row(chunk: Chunk, score: float) -> Json:
    row = asdict(chunk)
    row.pop("extra")
    row["score"] = round(score, 3)
    return row


def rank(scored: list[tuple[float, Chunk]], top_k: int) -> list[Json]:
    """가장 높은 점수를 1로 맞춘 뒤 정렬한다. 점수가 같으면 기본 근거(primary)를 앞에 둔다 (docs/MCP.md 4.1)."""
    scored = [(s, c) for s, c in scored if s > 0]
    if not scored:
        return []
    top = max(s for s, _ in scored)
    scored.sort(key=lambda sc: (-round(sc[0], 6), sc[1].basis != "primary"))
    return [chunk_row(c, s / top) for s, c in scored[:top_k]]


def doc_summary(doc_id: str, title: str, doc_type: str, version: Any, updated: Any) -> Json:
    return {"doc_id": doc_id, "title": title, "doc_type": doc_type, "version": version,
            "updated": str(updated or ""), "uri": f"doc://{doc_id}"}


class InMemoryKeywordStore:
    def __init__(self, docs: list[Doc]) -> None:
        self._docs = {d.doc_id: d for d in docs}
        self._chunks: list[Chunk] = [c for d in docs for c in chunk_doc(d)]
        self._bm25 = Bm25([c.embed_text for c in self._chunks])
        updated = sorted(d.updated for d in docs if d.updated)
        self._as_of = f"{updated[-1]}T00:00:00+09:00" if updated else None

    @property
    def chunks(self) -> list[Chunk]:
        return self._chunks

    def search(self, query: str, top_k: int, doc_types: list[str] | None) -> list[Json]:
        q = tokenize(query)
        if not q:
            return []
        scored = [(self._bm25.score(i, q), c) for i, c in enumerate(self._chunks)
                  if not doc_types or c.doc_type in doc_types]
        return rank(scored, top_k)

    def get_doc_raw(self, doc_id: str) -> str | None:
        doc = self._docs.get(doc_id)
        return doc.raw if doc else None

    def list_docs(self) -> list[Json]:
        return [doc_summary(d.doc_id, d.title, d.doc_type, d.meta["version"], d.updated) for d in self._docs.values()]

    def info(self) -> Json:
        return {"as_of": self._as_of, "source": "memory:keyword-bm25", "embedding": {"model": None, "dim": None}}
