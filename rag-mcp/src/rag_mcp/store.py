# Author: 정유은
# Date: 20OCT2026
# Purpose: 문서 검색 저장소 계약(DocStore)과 M1용 메모리 BM25 구현. 임베딩 모델이 정해지면 pgvector 구현으로 바꾼다.

"""문서 저장소.

M1은 메모리 안 키워드 검색(BM25, 한국어는 글자 2-gram)으로 대신한다.
임베딩 모델이 정해지면 같은 DocStore 계약으로 pgvector 구현(Embedding → Indexing)을 붙인다.
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
    def get_doc(self, doc_id: str) -> Doc | None: ...
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


class InMemoryKeywordStore:
    k1 = 1.2
    b = 0.75

    def __init__(self, docs: list[Doc]) -> None:
        self._docs = {d.doc_id: d for d in docs}
        self._chunks: list[Chunk] = [c for d in docs for c in chunk_doc(d)]
        # 제목·절 이름도 검색 대상에 넣는다.
        self._tf = [Counter(tokenize(f"{c.title} {c.section} {c.text}")) for c in self._chunks]
        self._len = [sum(tf.values()) for tf in self._tf]
        self._avg_len = sum(self._len) / max(len(self._len), 1)
        df: Counter[str] = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        n = len(self._chunks)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        verified = sorted(str(d.meta["verified_at"]) for d in docs)
        self._as_of = f"{verified[-1]}T00:00:00+09:00" if verified else None

    @property
    def chunks(self) -> list[Chunk]:
        return self._chunks

    def _bm25(self, i: int, query: list[str]) -> float:
        tf, length = self._tf[i], self._len[i]
        score = 0.0
        for t in set(query):
            if t not in tf:
                continue
            f = tf[t]
            score += self._idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * length / self._avg_len))
        return score

    def search(self, query: str, top_k: int, doc_types: list[str] | None) -> list[Json]:
        q = tokenize(query)
        if not q:
            return []
        scored = []
        for i, c in enumerate(self._chunks):
            if doc_types and c.doc_type not in doc_types:
                continue
            s = self._bm25(i, q)
            if s > 0:
                scored.append((s, c))
        if not scored:
            return []
        top = max(s for s, _ in scored)
        # 점수가 같으면 기본 근거(primary)를 앞에 둔다 (docs/MCP.md 4.1).
        scored.sort(key=lambda sc: (-round(sc[0], 6), sc[1].basis != "primary"))
        results = []
        for s, c in scored[:top_k]:
            row = asdict(c)
            row.pop("extra")
            row["score"] = round(s / top, 3)
            results.append(row)
        return results

    def get_doc(self, doc_id: str) -> Doc | None:
        return self._docs.get(doc_id)

    def list_docs(self) -> list[Json]:
        return [
            {"doc_id": d.doc_id, "title": d.title, "doc_type": d.doc_type,
             "version": d.meta["version"], "verified_at": str(d.meta["verified_at"]),
             "uri": f"doc://{d.doc_id}"}
            for d in self._docs.values()
        ]

    def info(self) -> Json:
        return {
            "as_of": self._as_of,
            "source": "memory:keyword-bm25-v0",
            "embedding": {"model": None, "dim": None},
        }
