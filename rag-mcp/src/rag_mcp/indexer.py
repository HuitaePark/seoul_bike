# Author: 정유은
# Date: 20OCT2026
# Purpose: rag-indexer. 근거 문서를 Chunking -> Embedding -> Indexing(pgvector)한다. 문서나 모델이 바뀔 때만 다시 만든다.

"""rag-indexer (ROLES 3 MCP·RAG: Chunking -> Embedding(모델 이름·차원 기록) -> Indexing(출처·버전)).

`docker compose run --rm rag-indexer` 또는 `rag-indexer [--docs DIR] [--force]`.

테이블 (vectordb, pgvector)
- `rag_doc`: 문서 원문과 머리말 (Resource `doc://{doc_id}`가 읽는다)
- `rag_chunk`: chunk 본문, 출처(citation·basis·section·version), 임베딩 `vector(dim)`, HNSW 코사인 색인
- `rag_index`: 색인을 만든 기록. 임베딩 모델 이름·차원, 문서 묶음 해시, 문서·chunk 수, 만든 시각

문서 묶음 해시와 모델이 직전 기록과 같으면 다시 만들지 않는다. 모델 차원이 바뀌면 `rag_chunk`를 새 차원으로 다시 만든다.
DB 접속은 libpq 환경 변수(PGHOST, PGUSER, PGPASSWORD, PGDATABASE) 또는 VECTOR_DSN.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg.types.json import Jsonb

from .docs import Doc, chunk_doc, load_docs
from .embedding import Embedder, make_embedder

DEFAULT_DOCS_DIR = Path(__file__).resolve().parents[3] / "docs" / "evidence"
EMBED_BATCH = 64


def connect(dsn: str | None = None) -> psycopg.Connection:
    conn = psycopg.connect(dsn or os.environ.get("VECTOR_DSN", ""), autocommit=False)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.commit()
    register_vector(conn)
    return conn


def corpus_hash(docs: list[Doc], model: str) -> str:
    h = hashlib.sha256(model.encode("utf-8"))
    for d in sorted(docs, key=lambda d: d.doc_id):
        h.update(d.doc_id.encode("utf-8"))
        h.update(d.content_hash.encode("utf-8"))
    return h.hexdigest()


def _chunk_dim(conn: psycopg.Connection) -> int | None:
    row = conn.execute(
        "SELECT a.atttypmod FROM pg_attribute a WHERE a.attrelid = to_regclass('rag_chunk') AND a.attname = 'embedding'"
    ).fetchone()
    return row[0] if row else None


def ensure_schema(conn: psycopg.Connection, dim: int) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rag_index (
            id          bigserial PRIMARY KEY,
            model       text        NOT NULL,
            dim         integer     NOT NULL,
            corpus_hash text        NOT NULL,
            doc_count   integer     NOT NULL,
            chunk_count integer     NOT NULL,
            built_at    timestamptz NOT NULL DEFAULT now()
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rag_doc (
            doc_id   text PRIMARY KEY,
            title    text NOT NULL,
            doc_type text NOT NULL,
            version  integer NOT NULL,
            updated  text,
            meta     jsonb NOT NULL,
            raw      text NOT NULL
        )""")
    if _chunk_dim(conn) not in (None, dim):
        conn.execute("DROP TABLE rag_chunk")  # 모델 차원이 바뀌면 벡터 열을 새로 만든다
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS rag_chunk (
            chunk_id    text PRIMARY KEY,
            doc_id      text    NOT NULL REFERENCES rag_doc(doc_id) ON DELETE CASCADE,
            doc_type    text    NOT NULL,
            title       text    NOT NULL,
            section     text    NOT NULL,
            text        text    NOT NULL,
            basis       text    NOT NULL,
            citation    text    NOT NULL,
            data_period text,
            status      text,
            doc_version integer NOT NULL,
            uri         text    NOT NULL,
            seq         integer NOT NULL,
            embedding   vector({int(dim)}) NOT NULL
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS rag_chunk_embedding_hnsw ON rag_chunk USING hnsw (embedding vector_cosine_ops)")
    conn.execute("CREATE INDEX IF NOT EXISTS rag_chunk_doc_type ON rag_chunk (doc_type)")


def last_index(conn: psycopg.Connection) -> dict[str, Any] | None:
    if conn.execute("SELECT to_regclass('rag_index')").fetchone()[0] is None:
        return None
    row = conn.execute(
        "SELECT model, dim, corpus_hash, doc_count, chunk_count, built_at FROM rag_index ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return dict(zip(("model", "dim", "corpus_hash", "doc_count", "chunk_count", "built_at"), row))


def build_index(conn: psycopg.Connection, docs: list[Doc], embedder: Embedder, *, force: bool = False) -> dict[str, Any]:
    digest = corpus_hash(docs, embedder.model)
    prev = last_index(conn)
    if not force and prev and prev["corpus_hash"] == digest and prev["dim"] == embedder.dim:
        return {"skipped": True, "reason": "문서와 모델이 직전 색인과 같다", **{k: str(v) for k, v in prev.items()}}

    chunks = [c for d in docs for c in chunk_doc(d)]
    texts = [c.embed_text for c in chunks]
    vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        vectors += embedder.embed_documents(texts[i : i + EMBED_BATCH])

    with conn.transaction():
        ensure_schema(conn, embedder.dim)
        conn.execute("DELETE FROM rag_chunk")
        conn.execute("DELETE FROM rag_doc")
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO rag_doc (doc_id, title, doc_type, version, updated, meta, raw) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                [(d.doc_id, d.title, d.doc_type, int(d.meta["version"]), d.updated,
                  Jsonb(json.loads(json.dumps(d.meta, default=str))), d.raw) for d in docs],
            )
            cur.executemany(
                """INSERT INTO rag_chunk (chunk_id, doc_id, doc_type, title, section, text, basis, citation, data_period,
                                          status, doc_version, uri, seq, embedding)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                [(c.chunk_id, c.doc_id, c.doc_type, c.title, c.section, c.text, c.basis, c.citation, c.data_period,
                  c.status, c.version, c.uri, seq, np.asarray(vec, dtype=np.float32)) for seq, (c, vec) in enumerate(zip(chunks, vectors))],
            )
        conn.execute(
            "INSERT INTO rag_index (model, dim, corpus_hash, doc_count, chunk_count) VALUES (%s,%s,%s,%s,%s)",
            (embedder.model, embedder.dim, digest, len(docs), len(chunks)),
        )
    return {"skipped": False, "model": embedder.model, "dim": embedder.dim, "doc_count": len(docs),
            "chunk_count": len(chunks), "corpus_hash": digest, "docs": [d.doc_id for d in docs]}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="rag-indexer", description="근거 문서를 pgvector에 색인한다")
    parser.add_argument("--docs", default=os.environ.get("RAG_DOCS_DIR", str(DEFAULT_DOCS_DIR)))
    parser.add_argument("--dsn", default=None, help="비우면 VECTOR_DSN 또는 PG* 환경 변수")
    parser.add_argument("--force", action="store_true", help="문서가 같아도 다시 만든다")
    args = parser.parse_args(argv)

    docs = load_docs(Path(args.docs))
    embedder = make_embedder()
    with connect(args.dsn) as conn:
        result = build_index(conn, docs, embedder, force=args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
