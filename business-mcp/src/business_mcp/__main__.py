# Author: 정유은
# Date: 20OCT2026
# Purpose: business-mcp를 Streamable HTTP로 실행한다. INTERNAL_MODE로 가짜·실제 백엔드를 고른다.

"""business-mcp 실행: Streamable HTTP, 기본 http://0.0.0.0:9101/mcp

환경 변수
- INTERNAL_MODE: fake(기본, M1) | http
- BACKEND_URL: http 모드의 백엔드 주소 (기본 http://backend:8080)
- INTERNAL_TOKEN: http 모드에서 X-Internal-Token 헤더로 보낼 서비스 토큰
- MCP_HOST, MCP_PORT
"""

from __future__ import annotations

import os

from .fake_internal import FakeInternalApi
from .internal import HttpInternalApi, InternalApi
from .server import build_server


def make_internal() -> InternalApi:
    mode = os.environ.get("INTERNAL_MODE", "fake")
    if mode == "fake":
        return FakeInternalApi()
    if mode == "http":
        return HttpInternalApi(os.environ.get("BACKEND_URL", "http://backend:8080"),
                               token=os.environ.get("INTERNAL_TOKEN"))
    raise SystemExit(f"INTERNAL_MODE는 fake 또는 http여야 합니다: {mode}")


def main() -> None:
    server = build_server(make_internal())
    server.run(
        transport="http",
        host=os.environ.get("MCP_HOST", "0.0.0.0"),
        port=int(os.environ.get("MCP_PORT", "9101")),
        path="/mcp",
    )


if __name__ == "__main__":
    main()
