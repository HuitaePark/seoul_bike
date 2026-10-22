# Author: Cheon Seonghun
# Date: 20OCT2026
# Purpose: Context: shift-state brief in the system prompt and summarisation when the token budget is exceeded.

"""Context (docs/HARNESS.md section 3.3)."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse, SummarizationMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from .config import ContextSettings
from .task import HarnessContext


class StateBriefMiddleware(AgentMiddleware):
    """Appends the task's shift brief (built by code, numbers included verbatim) to the system prompt."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | AIMessage:
        ctx: HarnessContext = request.runtime.context
        if not ctx.task.brief:
            return await handler(request)
        base = request.system_prompt or ""
        brief = ctx.masker.text(ctx.task.brief)
        return await handler(request.override(system_message=SystemMessage(f"{base}\n\n## 현재 근무조 상태\n{brief}")))


class ApprovedChangeNoteMiddleware(AgentMiddleware):
    """Right after an approved change ran, tells the model so in the system prompt (this call only).

    A change tool only runs once a person approved its card, but the tool result alone does not say that, and models
    then answer "please approve" or stop before the follow-up step (recompute after an approved constraint).
    The note is written by code from the message history; nothing is stored."""

    def __init__(self, gated: Iterable[str]) -> None:
        super().__init__()
        self.gated = frozenset(gated)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | AIMessage:
        last = request.messages[-1] if request.messages else None
        if not (isinstance(last, ToolMessage) and last.name in self.gated and last.status != "error"):
            return await handler(request)
        note = (
            f"## 방금 일어난 일\n`{last.name}` 호출은 사람이 승인해 이미 실행됐다 (결과는 마지막 도구 메시지). "
            "승인을 다시 요청하지 말고, 이 작업의 끝나는 조건을 다시 보고 남은 단계가 있으면 이어서 도구를 부른다. "
            "남은 단계가 없을 때만 실행 결과를 정리한다."
        )
        base = request.system_prompt or ""
        return await handler(request.override(system_message=SystemMessage(f"{base}\n\n{note}")))


def summarization(model: BaseChatModel, settings: ContextSettings) -> SummarizationMiddleware:
    """Over budget: older messages become one summary; tool call/result pairs are kept together by the library."""
    return SummarizationMiddleware(
        model, trigger=("tokens", settings.token_budget), keep=("tokens", settings.keep_tokens)
    )
