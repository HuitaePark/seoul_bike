# Author: Jung Minkyu
# Date: 22OCT2026
# Purpose: 추론 기록(관리자 로그). 모델이 도구를 고른 이유·인자·결과와 안전장치의 판단을 한 줄씩 남긴다 (asyncapi ai.traces).

"""Decision trace (docs/HARNESS.md section 3.9, asyncapi `AgentTraceMessage`).

What the admin log shows for one task segment, in order::

    RUN_START   task, model, offered and withheld tools, limits
    DECISION    the model's choice: thought, tool calls with their `rationale`, or the final answer
    GUARD       argument validation, repeated calls, turn and time limits
    TOOL_CALL   arguments as run (masked; Harness-filled ids marked)
    TOOL_RESULT ok or error, time, size, masked preview
    RECOVERY    retries and the switch to the fallback model
    APPROVAL_WAIT / RUN_END

The model explains every tool call in a `rationale` argument. Harness adds it to every tool schema the model sees
and strips it again right after the model answers (`DecisionTraceMiddleware`), so argument validation, approval
cards, the same-call limit, MCP servers and the backend see exactly the arguments they saw before.

Trace events are best effort and separate from progress events (`ai.events`): they never change what a task does,
and every value is masked like the rest of Harness output before it leaves the process.
"""
from __future__ import annotations

import copy
import json
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool

if TYPE_CHECKING:
    from .task import HarnessContext

TraceKind = Literal[
    "RUN_START", "ROUTE", "DECISION", "GUARD", "TOOL_CALL", "TOOL_RESULT", "RECOVERY", "APPROVAL_WAIT", "RUN_END"
]
TraceSource = Literal["GRAPH", "HARNESS", "CODE"]
SCHEMA_VERSION = 1
TITLE_MAX = 300

RATIONALE = "rationale"
RATIONALE_SCHEMA = {
    "type": "string",
    "description": (
        "이 도구를 지금 고른 이유 한두 문장: 무엇을 확인하거나 하려는지, 다른 도구가 아니라 이 도구인 까닭. "
        "실행에는 쓰이지 않고 관리자 추론 기록에만 남는다"
    ),
}


@dataclass(frozen=True)
class TraceEvent:
    """One `ai.traces` message (asyncapi `AgentTraceMessage`). `title` is the console line, `detail` the rest."""

    kind: TraceKind
    source: TraceSource
    task_id: str
    trace_id: str
    shift_id: str
    title: str
    detail: dict[str, Any] = field(default_factory=dict)
    run_id: str | None = None
    turn: int | None = None
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_json(self) -> dict[str, Any]:
        return {
            "eventId": self.event_id,
            "schemaVersion": SCHEMA_VERSION,
            "occurredAt": self.occurred_at,
            "shiftId": self.shift_id,
            "traceId": self.trace_id,
            "taskId": self.task_id,
            "runId": self.run_id,
            "source": self.source,
            "kind": self.kind,
            "turn": self.turn,
            "title": clip(self.title, TITLE_MAX),
            "detail": json.loads(json.dumps(self.detail, ensure_ascii=False, default=str)),
        }


@runtime_checkable
class TraceSink(Protocol):
    """Kafka `ai.traces` in the agent server, a list in tests. Must never raise into the caller."""

    def trace(self, event: TraceEvent) -> None: ...


class NullTrace:
    def trace(self, event: TraceEvent) -> None:
        return None


def clip(text: str | None, limit: int) -> str:
    """Shortens text for the log and says how much was cut."""
    text = text or ""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…(+{len(text) - limit}자)"


def _value_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def call_text(name: str, args: Mapping[str, Any], limit: int = 160) -> str:
    """`business_get_station_detail(station_id=ST-2005)`: one console line for a tool call."""
    inner = ", ".join(f"{k}={_value_text(v)}" for k, v in args.items() if v not in (None, [], ""))
    return f"{name}({clip(inner, limit)})"


def with_rationale(tools: Iterable[BaseTool]) -> list[BaseTool]:
    """Adds the `rationale` argument (first, required) to every tool schema the model sees.

    Models write arguments in schema order, so asking for the reason first makes the model decide why before it
    fills the arguments. Harness strips it before anything runs (`split_rationale`)."""
    out = []
    for tool in tools:
        schema = tool.args_schema
        if isinstance(schema, dict) and RATIONALE not in schema.get("properties", {}):
            schema = copy.deepcopy(schema)
            schema["properties"] = {RATIONALE: RATIONALE_SCHEMA, **schema.get("properties", {})}
            schema["required"] = [RATIONALE, *schema.get("required", [])]
            tool = tool.model_copy(update={"args_schema": schema})
        out.append(tool)
    return out


def split_rationale(ai: AIMessage) -> tuple[AIMessage, dict[str, str]]:
    """The AI message without `rationale` arguments, and the rationale of each tool call by call id."""
    if not ai.tool_calls or not any(RATIONALE in tc["args"] for tc in ai.tool_calls):
        return ai, {}
    reasons: dict[str, str] = {}
    calls = []
    for tc in ai.tool_calls:
        args = dict(tc["args"])
        reason = args.pop(RATIONALE, None)
        if reason is not None:
            reasons[tc["id"]] = str(reason).strip()
        calls.append({**tc, "args": args})
    # Providers keep the raw call JSON too (OpenAI `additional_kwargs.tool_calls`); drop it so the next request is
    # rebuilt from the stripped calls and no provider replays the rationale back as an argument.
    extra = {k: v for k, v in ai.additional_kwargs.items() if k not in {"tool_calls", "function_call"}}
    return ai.model_copy(update={"tool_calls": calls, "additional_kwargs": extra}), reasons


def thought_of(ai: AIMessage) -> str:
    """What the model said alongside its choice: provider reasoning blocks, plus text written next to tool calls."""
    parts: list[str] = []
    try:
        blocks = ai.content_blocks
    except Exception:  # noqa: BLE001 - unknown provider formats only lose the reasoning text
        blocks = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "reasoning" and block.get("reasoning"):
            parts.append(str(block["reasoning"]).strip())
    if ai.tool_calls and ai.text.strip():
        parts.append(ai.text.strip())
    return "\n".join(p for p in parts if p)


def _last_ai(response: ModelResponse | AIMessage) -> AIMessage | None:
    if isinstance(response, AIMessage):
        return response
    return next((m for m in reversed(response.result) if isinstance(m, AIMessage)), None)


def _replace(response: ModelResponse | AIMessage, old: AIMessage, new: AIMessage) -> ModelResponse | AIMessage:
    if isinstance(response, AIMessage):
        return new
    return ModelResponse(result=[new if m is old else m for m in response.result],
                         structured_response=response.structured_response)


class DecisionTraceMiddleware(AgentMiddleware):
    """Model calls: records the model's decision (DECISION) and strips `rationale` from its tool calls.

    Sits inside ArgumentValidation, so validation and everything after it see the calls without `rationale`.
    The rationale of each call is kept in the segment context for the TOOL_CALL line (ToolResultMiddleware)."""

    def __init__(self, preview_chars: int) -> None:
        super().__init__()
        self.preview_chars = preview_chars

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | AIMessage:
        ctx: HarnessContext = request.runtime.context
        started = ctx.clock()
        response = await handler(request)
        ai = _last_ai(response)
        if ai is None:
            return response
        stripped, reasons = split_rationale(ai)
        if stripped is not ai:
            response = _replace(response, ai, stripped)
        ctx.rationales.update(reasons)
        self._record(ctx, request, stripped, reasons, ctx.clock() - started)
        return response

    def _record(self, ctx: HarnessContext, request: ModelRequest, ai: AIMessage, reasons: dict[str, str],
                seconds: float) -> None:
        turn = ctx.stats.turns
        usage = getattr(ai, "usage_metadata", None) or {}
        calls = [{"id": tc["id"], "name": tc["name"], "args": tc["args"], "rationale": reasons.get(tc["id"])}
                 for tc in ai.tool_calls]
        detail: dict[str, Any] = {
            "attempt": ctx.decision_attempt(turn),
            "model": ctx.last_model,
            "fallback": ctx.last_model_is_fallback,
            "latencyMs": round(seconds * 1000),
            "inputTokens": usage.get("input_tokens"),
            "outputTokens": usage.get("output_tokens"),
            "offered": len(request.tools or []),
            "calls": calls,
        }
        if thought := thought_of(ai):
            detail["thought"] = clip(thought, self.preview_chars)
        if not calls:
            detail["answer"] = clip(ai.text, self.preview_chars)
        if calls:
            first = calls[0]
            title = f"선택: {call_text(first['name'], first['args'])}"
            if len(calls) > 1:
                title += f" 외 {len(calls) - 1}개"
            if first["rationale"]:
                title += f" — {first['rationale']}"
        else:
            title = "도구를 더 부르지 않고 답변을 쓴다"
        ctx.trace("DECISION", title, detail, turn=turn)
