# Author: 정유은
# Date: 20OCT2026
# Purpose: rag-mcp를 Streamable HTTP로 실행한다. pgvector 색인(rag-indexer가 만든 것) 또는 파일 메모리 색인을 쓴다.

"""rag-mcp 실행: Streamable HTTP, 기본 http://0.0.0.0:9102/mcp

환경 변수
- RAG_STORE: pgvector(기본, VECTOR_DSN 또는 PGHOST가 있을 때) | memory(파일을 읽어 메모리 BM25)
- EMBED_PROVIDER, EMBED_MODEL, FASTEMBED_CACHE_PATH: rag-indexer와 같은 값을 써야 한다 (embedding.py)
- VECTOR_DSN 또는 PGHOST·PGUSER·PGPASSWORD·PGDATABASE: pgvector 접속
- RAG_DOCS_DIR: memory 저장소의 근거 문서 폴더 (기본: 저장소의 docs/evidence/)
- MCP_HOST, MCP_PORT
"""

from __future__ import annotations

import os
from pathlib import Path

from .docs import load_docs
from .server import build_server
from .store import DocStore, InMemoryKeywordStore

DEFAULT_DOCS_DIR = Path(__file__).resolve().parents[3] / "docs" / "evidence"


def make_store() -> DocStore:
    default = "pgvector" if (os.environ.get("VECTOR_DSN") or os.environ.get("PGHOST")) else "memory"
    kind = os.environ.get("RAG_STORE", default)
    if kind == "memory":
        return InMemoryKeywordStore(load_docs(Path(os.environ.get("RAG_DOCS_DIR", DEFAULT_DOCS_DIR))))
    if kind == "pgvector":
        from .embedding import make_embedder
        from .indexer import connect
        from .pgvector_store import PgVectorStore

        return PgVectorStore(connect, make_embedder())
    raise SystemExit(f"RAG_STORE는 pgvector 또는 memory여야 합니다: {kind}")


def main() -> None:
    build_server(make_store()).run(
        transport="http",
        host=os.environ.get("MCP_HOST", "0.0.0.0"),
        port=int(os.environ.get("MCP_PORT", "9102")),
        path="/mcp",
    )


if __name__ == "__main__":
    main()
