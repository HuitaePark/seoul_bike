# Author: 정유은
# Date: 20OCT2026
# Purpose: pgvector 저장소. 질문을 같은 모델로 임베딩해 코사인 유사도로 찾고, 키워드(BM25) 점수를 섞어 순위를 정한다.

"""pgvector 검색 저장소 (DocStore 계약).

검색 순서
1. 질문을 색인과 **같은 모델**로 임베딩한다. 시작할 때 `rag_index`의 모델 이름·차원이 다르면 실행하지 않는다.
2. `rag_chunk`에서 코사인 거리(`<=>`, HNSW 색인)로 후보 `max(top_k*4, 20)`개를 고른다.
3. 후보마다 혼합 점수 = 0.7 x 코사인 유사도 + 0.3 x 키워드 점수(BM25, 후보 중 최댓값으로 나눔).
   대여소 ID(ST-1023)나 숫자처럼 의미 임베딩이 약한 낱말을 키워드 점수가 보완한다.
4. 가장 높은 점수를 1로 맞추고, 같으면 기본 근거(primary)를 앞에 둔다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import psycopg

from .docs import Chunk
from .embedding import Embedder
from .indexer import last_index
from .store import Bm25, doc_summary, rank, tokenize

Json = dict[str, Any]
VECTOR_WEIGHT = 0.7
KEYWORD_WEIGHT = 0.3

_CHUNK_COLS = "chunk_id, doc_id, doc_type, title, section, text, basis, citation, data_period, doc_version, uri, status"


def _chunk(row: tuple) -> Chunk:
    cid, doc_id, doc_type, title, section, text, basis, citation, data_period, version, uri, status = row
    return Chunk(chunk_id=cid, doc_id=doc_id, doc_type=doc_type, title=title, section=section, text=text,
                 basis=basis, citation=citation, data_period=data_period, version=version, uri=uri, status=status)


class IndexMismatch(RuntimeError):
    pass


class PgVectorStore:
    def __init__(self, connect: Callable[[], psycopg.Connection], embedder: Embedder) -> None:
        self._connect = connect
        self._conn = connect()
        conn = self._conn
        self._embedder = embedder
        index = last_index(conn)
        if index is None:
            raise IndexMismatch("색인이 없습니다. rag-indexer를 먼저 실행하세요.")
        if index["model"] != embedder.model or index["dim"] != embedder.dim:
            raise IndexMismatch(
                f"색인 모델({index['model']}, {index['dim']}차원)과 검색 모델({embedder.model}, {embedder.dim}차원)이 "
                "다릅니다. 같은 EMBED_MODEL로 rag-indexer를 다시 실행하세요."
            )
        self._index = index
        rows = conn.execute(f"SELECT {_CHUNK_COLS} FROM rag_chunk ORDER BY seq").fetchall()
        self._chunks = [_chunk(r) for r in rows]
        self._pos = {c.chunk_id: i for i, c in enumerate(self._chunks)}
        self._bm25 = Bm25([c.embed_text for c in self._chunks])

    def _read(self, sql: str, params: tuple = ()) -> list[tuple]:
        """읽기 전용 질의. 연결이 끊겼으면 한 번 다시 연결한다."""
        for attempt in (0, 1):
            try:
                rows = self._conn.execute(sql, params).fetchall()
                self._conn.rollback()  # 읽기만 했으므로 트랜잭션을 닫는다
                return rows
            except psycopg.OperationalError:
                if attempt:
                    raise
                self._conn = self._connect()
        return []

    def search(self, query: str, top_k: int, doc_types: list[str] | None) -> list[Json]:
        if not query.strip():
            return []
        qvec = np.asarray(self._embedder.embed_query(query), dtype=np.float32)
        limit = max(top_k * 4, 20)
        rows = self._read(
            f"""SELECT {_CHUNK_COLS}, 1 - (embedding <=> %s) AS cosine FROM rag_chunk
                WHERE %s::text[] IS NULL OR doc_type = ANY(%s::text[])
                ORDER BY embedding <=> %s LIMIT %s""",
            (qvec, doc_types or None, doc_types or None, qvec, limit),
        )
        q = tokenize(query)
        keyword = {r[0]: self._bm25.score(self._pos[r[0]], q) for r in rows if r[0] in self._pos}
        kmax = max(keyword.values(), default=0.0) or 1.0
        scored = [(VECTOR_WEIGHT * max(float(r[-1]), 0.0) + KEYWORD_WEIGHT * keyword.get(r[0], 0.0) / kmax, _chunk(r[:-1]))
                  for r in rows]
        return rank(scored, top_k)

    def get_doc_raw(self, doc_id: str) -> str | None:
        rows = self._read("SELECT raw FROM rag_doc WHERE doc_id = %s", (doc_id,))
        return rows[0][0] if rows else None

    def list_docs(self) -> list[Json]:
        rows = self._read("SELECT doc_id, title, doc_type, version, updated FROM rag_doc ORDER BY doc_id")
        return [doc_summary(*r) for r in rows]

    def info(self) -> Json:
        return {
            "as_of": self._index["built_at"].isoformat(timespec="seconds"),
            "source": "pgvector:rag_chunk (cosine + bm25)",
            "embedding": {"model": self._index["model"], "dim": self._index["dim"]},
        }
