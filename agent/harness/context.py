# Author: Cheon Seonghun
# Date: 20OCT2026
# Purpose: Context: shift-state brief in the system prompt and summarisation when the token budget is exceeded.

"""Context (docs/HARNESS.md section 3.3)."""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse, SummarizationMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage

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


def summarization(model: BaseChatModel, settings: ContextSettings) -> SummarizationMiddleware:
    """Over budget: older messages become one summary; tool call/result pairs are kept together by the library."""
    return SummarizationMiddleware(
        model, trigger=("tokens", settings.token_budget), keep=("tokens", settings.keep_tokens)
    )
