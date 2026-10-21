# Author: Cheon Seonghun
# Date: 20OCT2026
# Purpose: Agent Loop limits: model turns, wall time, and repeated identical tool calls per execution segment.

"""Agent Loop limits (docs/HARNESS.md section 3.5)."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Coroutine

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from .config import LoopSettings
from .errors import FailureCode, HarnessFailure
from .task import HarnessContext


def call_signature(name: str, args: dict) -> str:
    return f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"


class LoopGuardMiddleware(AgentMiddleware):
    """Outermost middleware. Limits count from `HarnessContext.stats`, which is fresh for every segment.

    - turns: the (max_turns + 1)-th model call fails with LOOP_MAX_TURNS.
    - time: model and tool calls are cancelled when the segment budget for the task type runs out
      (LOOP_TIMEOUT). field_report and plan_draft follow NFR-01/NFR-02, others the default.
    - same tool: the call after max_same_tool_calls identical calls is answered with an error ToolMessage
      instead of running; one more identical call fails with LOOP_SAME_TOOL.
    """

    def __init__(self, settings: LoopSettings) -> None:
        super().__init__()
        self.settings = settings

    def _timeout(self, ctx: HarnessContext, limit: float) -> HarnessFailure:
        ctx.trace("GUARD", f"시간 한도 {limit:g}초를 넘겨 진행 중 호출을 끊는다",
                  {"rule": "TIMEOUT", "action": "FAIL", "limitS": limit, "elapsedS": round(ctx.elapsed(), 3)},
                  turn=ctx.stats.turns or None)
        return HarnessFailure(FailureCode.LOOP_TIMEOUT, f"작업 시간 {limit:g}초 초과")

    async def _within_budget[T](self, ctx: HarnessContext, call: Coroutine[object, object, T]) -> T:
        limit = self.settings.seconds_for(ctx.task.task_type)
        remaining = limit - ctx.elapsed()
        if remaining <= 0:
            call.close()
            raise self._timeout(ctx, limit)
        try:
            return await asyncio.wait_for(call, remaining)
        except TimeoutError as exc:
            raise self._timeout(ctx, limit) from exc

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | AIMessage:
        ctx: HarnessContext = request.runtime.context
        ctx.stats.turns += 1
        if ctx.stats.turns > self.settings.max_turns:
            ctx.trace("GUARD", f"모델 호출이 {self.settings.max_turns}턴을 넘어 작업을 끝낸다",
                      {"rule": "MAX_TURNS", "action": "FAIL", "limit": self.settings.max_turns}, turn=ctx.stats.turns)
            raise HarnessFailure(FailureCode.LOOP_MAX_TURNS, f"모델 호출 {self.settings.max_turns}턴 초과")
        return await self._within_budget(ctx, handler(request))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        ctx: HarnessContext = request.runtime.context
        call = request.tool_call
        signature = call_signature(call["name"], call["args"])
        ctx.stats.same_tool[signature] += 1
        count = ctx.stats.same_tool[signature]
        limit = self.settings.max_same_tool_calls
        if count > limit + 1:
            ctx.trace("GUARD", f"{call['name']}을 같은 인자로 {count}번째 불러 작업을 끝낸다",
                      {"rule": "SAME_TOOL", "action": "FAIL", "tool": call["name"], "count": count, "limit": limit},
                      turn=ctx.stats.turns or None)
            raise HarnessFailure(FailureCode.LOOP_SAME_TOOL, f"{call['name']}을 같은 인자로 {count}회 호출")
        if count == limit + 1:
            ctx.trace("GUARD", f"{call['name']} 같은 인자 {count}번째 호출은 실행하지 않고 모델에 되돌린다",
                      {"rule": "SAME_TOOL", "action": "BLOCK", "tool": call["name"], "count": count, "limit": limit},
                      turn=ctx.stats.turns or None)
            return ToolMessage(
                content=(
                    f"실행하지 않음: {call['name']}을 같은 인자로 이미 {limit}회 호출했다. "
                    "앞선 결과를 쓰거나 다른 방법을 택한다. 한 번 더 반복하면 작업이 중단된다."
                ),
                tool_call_id=call["id"],
                name=call["name"],
                status="error",
            )
        ctx.stats.tool_calls += 1
        return await self._within_budget(ctx, handler(request))
