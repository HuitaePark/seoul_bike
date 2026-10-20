# Author: Cheon Seonghun
# Date: 20OCT2026
# Purpose: Recovery: retry 429/503 with backoff, then switch to the fallback model, else fail with a code.

"""Recovery (docs/HARNESS.md section 3.7)."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langgraph.errors import GraphBubbleUp

from .config import RecoverySettings
from .errors import FailureCode, HarnessFailure
from .task import HarnessContext

Sleep = Callable[[float], Awaitable[None]]


def status_code(exc: BaseException) -> int | None:
    """HTTP status from provider SDK errors (OpenAI, Anthropic, httpx all expose one of these)."""
    for candidate in (exc, getattr(exc, "response", None)):
        code = getattr(candidate, "status_code", None)
        if isinstance(code, int):
            return code
    return None


class RecoveryMiddleware(AgentMiddleware):
    """Innermost model middleware.

    Primary model: up to `model_retries` retries on retryable status codes. Then the fallback model with the
    same retry budget. Non-retryable errors (400, auth, ...) fail at once with MODEL_ERROR and do not fall back,
    because the fallback would receive the same bad request.
    """

    def __init__(self, settings: RecoverySettings, fallback: BaseChatModel | None, sleep: Sleep = asyncio.sleep):
        super().__init__()
        self.settings = settings
        self.fallback = fallback
        self.sleep = sleep

    def _delay(self, attempt: int) -> float:
        delays = self.settings.backoff_seconds
        return delays[min(attempt, len(delays) - 1)] if delays else 0.0

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | AIMessage:
        ctx: HarnessContext = request.runtime.context
        candidates = [request] + ([request.override(model=self.fallback)] if self.fallback else [])
        last: BaseException | None = None
        for index, candidate in enumerate(candidates):
            if index > 0:
                ctx.stats.fallback = True
            for attempt in range(self.settings.model_retries + 1):
                try:
                    return await handler(candidate)
                except (GraphBubbleUp, HarnessFailure, asyncio.CancelledError):
                    raise
                except Exception as exc:
                    if status_code(exc) not in self.settings.retry_status:
                        raise HarnessFailure(FailureCode.MODEL_ERROR, f"{type(exc).__name__}: {exc}") from exc
                    last = exc
                    if attempt < self.settings.model_retries:
                        ctx.stats.retries += 1
                        await self.sleep(self._delay(attempt))
        raise HarnessFailure(
            FailureCode.MODEL_UNAVAILABLE, f"재시도·대체 모델 모두 실패: {type(last).__name__}: {last}"
        ) from last
