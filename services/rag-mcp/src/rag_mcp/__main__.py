# Author: 정유은
# Date: 20OCT2026
# Purpose: rag-mcp를 Streamable HTTP로 실행한다. 시작할 때 근거 문서를 읽어 메모리 색인을 만든다.

"""rag-mcp 실행: Streamable HTTP, 기본 http://0.0.0.0:9102/mcp

환경 변수
- RAG_DOCS_DIR: 근거 문서 폴더 (기본: 저장소의 docs/rag/)
- MCP_HOST, MCP_PORT
"""

from __future__ import annotations

import os
from pathlib import Path

from .docs import load_docs
from .server import build_server
from .store import InMemoryKeywordStore

DEFAULT_DOCS_DIR = Path(__file__).resolve().parents[4] / "docs" / "rag"


def main() -> None:
    docs_dir = Path(os.environ.get("RAG_DOCS_DIR", DEFAULT_DOCS_DIR))
    store = InMemoryKeywordStore(load_docs(docs_dir))
    build_server(store).run(
        transport="http",
        host=os.environ.get("MCP_HOST", "0.0.0.0"),
        port=int(os.environ.get("MCP_PORT", "9102")),
        path="/mcp",
    )


if __name__ == "__main__":
    main()
