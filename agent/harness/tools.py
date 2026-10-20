# Author: Cheon Seonghun
# Date: 20OCT2026
# Purpose: Tools: MCP connection with server prefixes, per-task allow lists, result truncation and masking.

"""Tools (docs/HARNESS.md section 3.2)."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import MCPToolCallRequest, MCPToolCallResult
from langgraph.errors import GraphBubbleUp
from langgraph.types import Command

from .config import ToolSettings
from .errors import FailureCode, HarnessFailure
from .events import ProgressEvent
from .guardrails import destructive_tools
from .task import HarnessContext


class TraceHeaderInterceptor:
    """Adds the task's trace_id to every MCP HTTP call so business-mcp and backend logs share it."""

    def __init__(self, header: str) -> None:
        self.header = header

    async def __call__(
        self,
        request: MCPToolCallRequest,
        handler: Callable[[MCPToolCallRequest], Awaitable[MCPToolCallResult]],
    ) -> MCPToolCallResult:
        ctx = getattr(request.runtime, "context", None)
        if isinstance(ctx, HarnessContext):
            request = request.override(headers={**(request.headers or {}), self.header: ctx.task.trace_id})
        return await handler(request)


def mcp_client(servers: dict[str, str], trace_header: str) -> MultiServerMCPClient:
    """`servers` maps the prefix to the Streamable HTTP URL, e.g. {"business": "http://business-mcp:9101/mcp"}."""
    return MultiServerMCPClient(
        {name: {"transport": "streamable_http", "url": url} for name, url in servers.items()},
        tool_name_prefix=True,
        tool_interceptors=[TraceHeaderInterceptor(trace_header)],
    )


def select_tools(tools: Iterable[BaseTool], task_type: str, settings: ToolSettings) -> list[BaseTool]:
    """Tools the model may see for this task. Read-only tasks never get destructive tools, whatever the allow list says."""
    tools = list(tools)
    allowed = set(settings.allow.get(task_type, ()))
    if task_type in settings.read_only_tasks:
        allowed -= set(destructive_tools(tools))
    return [t for t in tools if t.name in allowed]


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content)


def truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return f"{text[:limit]}\n[잘림: 전체 {len(text)}자 중 {limit}자]", True


def scope_to_truck(value: Any, truck_id: str) -> Any:
    """Drops list entries that belong to another truck (objects with a different `truck_id`), at any depth."""
    if isinstance(value, list):
        return [
            scope_to_truck(v, truck_id)
            for v in value
            if not (isinstance(v, dict) and "truck_id" in v and v["truck_id"] != truck_id)
        ]
    if isinstance(value, dict):
        return {k: scope_to_truck(v, truck_id) for k, v in value.items()}
    return value


class ToolResultMiddleware(AgentMiddleware):
    """Wraps every tool call: TOOL_START/TOOL_END events, RAG top_k cap, driver scope, masking, truncation.

    For tasks a driver sent, other trucks' entries are removed from results (NFR-06). The MCP artifact
    (structured copy of the result) is dropped so unscoped, unmasked data never enters state or traces.
    """

    def __init__(self, settings: ToolSettings) -> None:
        super().__init__()
        self.settings = settings

    @staticmethod
    def _prepare(text: str, ctx: HarnessContext) -> str:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return ctx.masker.text(text)
        requester = ctx.task.requester
        if requester.role == "driver" and requester.truck_id:
            parsed = scope_to_truck(parsed, requester.truck_id)
        return json.dumps(ctx.masker.value(parsed), ensure_ascii=False)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        ctx: HarnessContext = request.runtime.context
        call = request.tool_call
        if call["name"] == self.settings.rag_tool and int(call["args"].get("top_k", 0)) > self.settings.rag_top_k:
            call = {**call, "args": {**call["args"], "top_k": self.settings.rag_top_k}}
            request = request.override(tool_call=call)
        ctx.sink.emit(
            ProgressEvent(
                "TOOL_START",
                ctx.task.task_id,
                ctx.task.trace_id,
                {"tool": call["name"], "args": ctx.masker.value(call["args"])},
            )
        )
        try:
            result = await handler(request)
        except (GraphBubbleUp, HarnessFailure, asyncio.CancelledError):
            raise
        except Exception as exc:
            # MCP isError results already arrive as error ToolMessages; anything raised here is transport failure.
            raise HarnessFailure(FailureCode.TOOL_UNAVAILABLE, f"{call['name']}: {type(exc).__name__}: {exc}") from exc
        if not isinstance(result, ToolMessage):
            return result
        content, cut = truncate(self._prepare(_text(result.content), ctx), self.settings.result_max_chars)
        ctx.stats.truncated += cut
        masked = result.model_copy(update={"content": content, "artifact": None})
        ctx.sink.emit(
            ProgressEvent(
                "TOOL_END",
                ctx.task.task_id,
                ctx.task.trace_id,
                {"tool": call["name"], "ok": masked.status != "error", "truncated": cut},
            )
        )
        return masked
