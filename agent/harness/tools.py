# Author: Cheon Seonghun
# Date: 20OCT2026
# Purpose: Tools: MCP connection, per-task allow lists, approval id injection, driver scope, truncation, masking.

"""Tools (docs/HARNESS.md section 3.2)."""
from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import MCPToolCallRequest, MCPToolCallResult
from langgraph.errors import GraphBubbleUp
from langgraph.types import Command

from .config import ApprovalRule, ToolSettings
from .contract import final_approval_id
from .errors import FailureCode, HarnessFailure
from .guardrails import destructive_tools
from .task import HarnessContext
from .trace import call_text, clip


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


def offered_tools(
    tools: Iterable[BaseTool], task_type: str, role: str, settings: ToolSettings
) -> tuple[list[BaseTool], list[dict[str, str]]]:
    """Tools the model may choose from for this task, and every loaded tool it may not, with the reason (trace).
    Read-only tasks never get destructive tools, and a driver's task never gets `driver_deny` tools, whatever the
    allow list says."""
    tools = list(tools)
    allowed = set(settings.allow.get(task_type, ()))
    destructive = set(destructive_tools(tools))
    offered, withheld = [], []
    for tool in tools:
        if tool.name not in allowed:
            why = "이 작업의 허용 목록에 없다"
        elif task_type in settings.read_only_tasks and tool.name in destructive:
            why = "읽기 전용 작업이라 변경 도구는 주지 않는다"
        elif role == "driver" and tool.name in settings.driver_deny:
            why = "기사 작업에는 주지 않는다 (NFR-06)"
        else:
            offered.append(tool)
            continue
        withheld.append({"name": tool.name, "why": why})
    return offered, withheld


def select_tools(tools: Iterable[BaseTool], task_type: str, role: str, settings: ToolSettings) -> list[BaseTool]:
    """Tools the model may see for this task (`offered_tools` without the reasons)."""
    return offered_tools(tools, task_type, role, settings)[0]


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
    """Drops list entries that belong to another truck (objects with a different `truckId`), at any depth."""
    if isinstance(value, list):
        return [
            scope_to_truck(v, truck_id)
            for v in value
            if not (isinstance(v, dict) and "truckId" in v and v["truckId"] != truck_id)
        ]
    if isinstance(value, dict):
        return {k: scope_to_truck(v, truck_id) for k, v in value.items()}
    return value


def hide_injected_args(tools: Iterable[BaseTool], names: Iterable[str]) -> list[BaseTool]:
    """Removes Harness-filled arguments (`approval_id`, `task_id`) from the schemas the model sees, so the model can
    neither see nor forge them. ToolResultMiddleware fills them in when the approved call runs."""
    names = set(names)
    out = []
    for tool in tools:
        schema = tool.args_schema
        if isinstance(schema, dict) and names & set(schema.get("properties", {})):
            schema = copy.deepcopy(schema)
            for name in names:
                schema["properties"].pop(name, None)
            schema["required"] = [r for r in schema.get("required", []) if r not in names]
            tool = tool.model_copy(update={"args_schema": schema})
        out.append(tool)
    return out


class ToolResultMiddleware(AgentMiddleware):
    """Wraps every tool call: TOOL_START/TOOL_END events, RAG top_k cap, approval id injection, driver scope,
    masking, truncation.

    Gated tools get `approval_id` (the last card of the call's approval order, e.g. the driver's acceptance of a
    route change) and `task_id`, so the backend can check every approval again (docs/specs internal-api). For tasks a driver sent, other trucks' entries
    are removed from results (NFR-06). The MCP artifact is dropped so unscoped, unmasked data never enters state.
    """

    def __init__(
        self,
        settings: ToolSettings,
        labels: dict[str, str],
        rules: Mapping[str, ApprovalRule],
        injected: Iterable[str],
        task_id_tools: Iterable[str] = (),
        preview_chars: int = 1200,
    ):
        super().__init__()
        self.settings = settings
        self.labels = labels
        self.rules = rules
        self.injected = tuple(injected)
        self.task_id_tools = frozenset(task_id_tools)
        self.preview_chars = preview_chars

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

    def _call_to_run(self, call: dict[str, Any], ctx: HarnessContext, history: list) -> dict[str, Any]:
        args = {k: v for k, v in call["args"].items() if k not in self.injected}
        if call["name"] == self.settings.rag_tool and int(args.get("top_k", 0)) > self.settings.rag_top_k:
            args["top_k"] = self.settings.rag_top_k
        if call["name"] in self.rules:
            args |= {"approval_id": final_approval_id(ctx.task, call, history, self.rules), "task_id": ctx.task.task_id}
        elif call["name"] in self.task_id_tools:
            args["task_id"] = ctx.task.task_id
        return {**call, "args": args}

    def _tool(self, name: str, ok: bool | None = None) -> dict[str, Any]:
        tool = {"name": name, "label": self.labels.get(name, name)}
        return tool if ok is None else {**tool, "ok": ok}

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        ctx: HarnessContext = request.runtime.context
        call = request.tool_call
        to_run = self._call_to_run(call, ctx, request.state.get("messages", []))
        if to_run["args"] != call["args"]:
            request = request.override(tool_call=to_run)
        name = call["name"]
        ctx.emit("TOOL_START", {"tool": self._tool(name)})
        approved = " · 승인된 호출" if name in self.rules else ""
        ctx.trace("TOOL_CALL", f"→ {call_text(name, to_run['args'])}{approved}", {
            "callId": call["id"], "name": name, "label": self.labels.get(name, name), "args": to_run["args"],
            "injected": sorted(set(to_run["args"]) - set(call["args"])), "gated": name in self.rules,
            "rationale": ctx.rationales.get(call["id"]),
        }, turn=ctx.stats.turns or None)
        started = ctx.clock()
        try:
            result = await handler(request)
        except (GraphBubbleUp, HarnessFailure, asyncio.CancelledError):
            raise
        except Exception as exc:
            # MCP isError results already arrive as error ToolMessages; anything raised here is transport failure.
            error = f"{type(exc).__name__}: {exc}"
            ctx.trace("TOOL_RESULT", f"← {name} 연결 실패: {clip(error, 120)}",
                      {"callId": call["id"], "name": name, "ok": False, "ms": round((ctx.clock() - started) * 1000),
                       "error": clip(error, self.preview_chars)}, turn=ctx.stats.turns or None)
            raise HarnessFailure(FailureCode.TOOL_UNAVAILABLE, f"{name}: {error}") from exc
        if not isinstance(result, ToolMessage):
            return result
        content, cut = truncate(self._prepare(_text(result.content), ctx), self.settings.result_max_chars)
        ctx.stats.truncated += cut
        masked = result.model_copy(update={"content": content, "artifact": None})
        ok = masked.status != "error"
        ctx.emit("TOOL_END", {"tool": self._tool(name, ok)}, {"truncated": cut})
        ms = round((ctx.clock() - started) * 1000)
        title = f"← {name} {'성공' if ok else '오류'} {ms / 1000:.2f}초, {len(content):,}자"
        if not ok:
            title += f": {clip(content, 120)}"
        ctx.trace("TOOL_RESULT", title, {
            "callId": call["id"], "name": name, "ok": ok, "ms": ms, "chars": len(content), "truncated": cut,
            "scoped": ctx.task.requester.role == "driver", "preview": clip(content, self.preview_chars),
        }, turn=ctx.stats.turns or None)
        return masked
