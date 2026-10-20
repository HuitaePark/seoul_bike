# Author: Cheon Seonghun
# Date: 20OCT2026
# Purpose: Observability: LangSmith run config keyed by trace_id, and token usage per segment.

"""Observability (docs/HARNESS.md section 3.8)."""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage

from .task import HarnessContext, Task


def run_config(task: Task, *, prompt_version: str, model: str, recursion_limit: int, extra: dict | None = None) -> dict[str, Any]:
    """LangGraph config for one segment. LangSmith picks up run_id, name, tags, and metadata automatically
    when `LANGSMITH_TRACING=true`; searching metadata `trace_id` finds every segment of a task."""
    return {
        "configurable": {"thread_id": task.thread_id},
        "run_id": uuid.uuid4(),
        "run_name": f"harness:{task.task_type}",
        "recursion_limit": recursion_limit,
        "tags": [task.task_type, prompt_version],
        "metadata": {
            "trace_id": task.trace_id,
            "task_id": task.task_id,
            "task_type": task.task_type,
            "thread_id": task.thread_id,
            "prompt_version": prompt_version,
            "model": model,
            **(extra or {}),
        },
    }


class UsageMiddleware(AgentMiddleware):
    """Adds provider token usage of every model response to the segment stats (shown in task history)."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | AIMessage:
        response = await handler(request)
        ctx: HarnessContext = request.runtime.context
        messages = [response] if isinstance(response, AIMessage) else response.result
        for message in messages:
            usage = getattr(message, "usage_metadata", None) or {}
            ctx.stats.input_tokens += usage.get("input_tokens", 0)
            ctx.stats.output_tokens += usage.get("output_tokens", 0)
        return response
