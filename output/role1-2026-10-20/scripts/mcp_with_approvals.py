"""business-mcp (role 3 branch code, fake internal API) + POST /_test/approve that records an approval the way the
backend would. Only for local integration checks; runs in the MCP venv.

    python mcp_with_approvals.py 9111
"""
import sys

import uvicorn
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from business_mcp.fake_internal import FakeInternalApi
from business_mcp.server import build_server

internal = FakeInternalApi()
app = build_server(internal).http_app(path="/mcp")


async def approve(request: Request) -> JSONResponse:
    body = await request.json()
    internal.approve(body["approvalId"], role=body["role"], task_id=body.get("taskId"),
                     candidate_id=body.get("candidateId"))
    return JSONResponse({"ok": True})


app.router.routes.append(Route("/_test/approve", approve, methods=["POST"]))
uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
