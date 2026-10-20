# Author: Cheon Seonghun
# Date: 20OCT2026
# Purpose: Scripted chat model and provider-style errors for deterministic Harness tests and fault scenarios.

"""Test doubles for the model side."""
from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

Step = AIMessage | Exception | Callable[[list[BaseMessage]], AIMessage]


class StatusError(Exception):
    """Mimics provider SDK errors that expose `status_code` (openai.RateLimitError, anthropic.APIStatusError)."""

    def __init__(self, status_code: int, message: str = "") -> None:
        super().__init__(message or f"HTTP {status_code}")
        self.status_code = status_code


def call(name: str, **args: Any) -> AIMessage:
    """An AI turn that calls one tool."""
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:8]}"}])


def say(text: str, input_tokens: int = 10, output_tokens: int = 5) -> AIMessage:
    return AIMessage(
        content=text,
        usage_metadata={"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": input_tokens + output_tokens},
    )


class ScriptedChatModel(BaseChatModel):
    """Returns the next scripted step for every call. A step may be a message, an exception to raise,
    or a function of the incoming messages (to react to tool results). Records every request it saw."""

    steps: list[Any] = Field(default_factory=list)
    seen: list[list[BaseMessage]] = Field(default_factory=list)
    label: str = "scripted"

    @property
    def _llm_type(self) -> str:
        return "scripted"

    @property
    def model_name(self) -> str:
        return self.label

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedChatModel:
        return self

    def _generate(self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        self.seen.append(list(messages))
        step: Step = self.steps.pop(0) if self.steps else say("작업을 마쳤다.")
        if isinstance(step, Exception):
            raise step
        if not isinstance(step, AIMessage):
            step = step(messages)
        # Fresh ids so a script can be reused across segments without id collisions.
        if step.tool_calls:
            step = step.model_copy(
                update={"tool_calls": [{**tc, "id": f"call_{uuid.uuid4().hex[:8]}"} for tc in step.tool_calls]}
            )
        return ChatResult(generations=[ChatGeneration(message=step)])
