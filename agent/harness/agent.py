# Author: Cheon Seonghun
# Date: 20OCT2026
# Purpose: HarnessAgent: assembles the 8 Harness elements around the model and runs one task segment.

"""Entry point the agent server calls for every `ai.tasks` message."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.types import Command
from langsmith.run_helpers import tracing_context
from langsmith.utils import tracing_is_enabled

from .config import HarnessConfig
from .context import StateBriefMiddleware, summarization
from .errors import FailureCode, HarnessFailure
from .events import EventSink, ProgressEvent
from .guardrails import Masker, approval_middleware, destructive_tools
from .loop import LoopGuardMiddleware
from .prompts import Prompt, load_prompt, task_message
from .recovery import RecoveryMiddleware, Sleep
from .task import HarnessContext, Outcome, Task
from .tools import ToolResultMiddleware, select_tools
from .tracing import UsageMiddleware, langsmith_client, run_config
from .validation import ArgumentValidationMiddleware

log = logging.getLogger(__name__)


def _model_name(model: BaseChatModel) -> str:
    return getattr(model, "model_name", None) or getattr(model, "model", None) or type(model).__name__


class HarnessAgent:
    """Model + Harness for one agent server process.

    Middleware order (outer → inner) for model calls: LoopGuard → ArgumentValidation → Usage → StateBrief → Recovery.
    Tool calls: LoopGuard → ToolResult. Hooks: Summarization before the model, approval interrupt after it.
    """

    def __init__(
        self,
        config: HarnessConfig,
        tools: Iterable[BaseTool],
        sink: EventSink,
        *,
        model: BaseChatModel | None = None,
        fallback_model: BaseChatModel | None = None,
        checkpointer: BaseCheckpointSaver | None = None,
        project: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.config = config
        self.tools = list(tools)
        self.sink = sink
        kwargs = config.models.kwargs
        self.model = model or init_chat_model(config.models.primary, **kwargs)
        self.fallback_model = fallback_model or (
            init_chat_model(config.models.fallback, **kwargs) if config.models.fallback else None
        )
        self.checkpointer = checkpointer or InMemorySaver()
        self.project = project or config.observability.project
        self.clock = clock
        self.sleep = sleep
        self._built: dict[str, tuple[Any, Prompt]] = {}
        # One masker per process (aliases are name hashes); also the LangSmith anonymizer (tracing.langsmith_client).
        self.masker = Masker(config.guardrails.mask_keys)
        self.trace_client = (
            langsmith_client(self.masker) if tracing_is_enabled() else None
        )

    @property
    def recursion_limit(self) -> int:
        # Backstop only: LoopGuard stops first. One turn is at most ~6 graph steps (hooks, model, tools).
        return self.config.loop.max_turns * 6 + 10

    def _agent(self, task_type: str) -> tuple[Any, Prompt]:
        if task_type not in self._built:
            cfg = self.config
            tools = select_tools(self.tools, task_type, cfg.tools)
            destructive = destructive_tools(tools)
            prompt = load_prompt(cfg.prompts_dir, task_type)
            middleware = [
                LoopGuardMiddleware(cfg.loop),
                ArgumentValidationMiddleware({t.name for t in tools}, cfg.guardrails.id_pattern),
                UsageMiddleware(),
                StateBriefMiddleware(),
                RecoveryMiddleware(cfg.recovery, self.fallback_model, self.sleep),
                ToolResultMiddleware(cfg.tools),
                summarization(self.model, cfg.context),
                approval_middleware(destructive, cfg.guardrails.approver),
            ]
            graph = create_agent(
                self.model,
                tools,
                system_prompt=prompt.text,
                middleware=middleware,
                context_schema=HarnessContext,
                checkpointer=self.checkpointer,
                name=f"harness_{task_type}",
            )
            self._built[task_type] = (graph, prompt)
        return self._built[task_type]

    def _masker(self, task: Task) -> Masker:
        self.masker.add_names(task.driver_names)
        return self.masker

    async def history(self, task: Task) -> list[BaseMessage]:
        """Masked message history of the task's thread (what the model saw), for evaluation and debugging."""
        graph, _ = self._agent(task.task_type)
        state = await graph.aget_state({"configurable": {"thread_id": task.thread_id}})
        return list(state.values.get("messages", []))

    async def start(self, task: Task) -> Outcome:
        """New task: requester and report values are written by code; free text is masked and wrapped as
        untrusted user input."""
        masker = self._masker(task)
        payload = {"messages": [HumanMessage(task_message(task, masker.text(task.input)))]}
        return await self._run(task, payload, masker, rejected=False)

    async def resume(self, task: Task, decisions: Sequence[dict[str, Any]]) -> Outcome:
        """Approval answer for the pending interrupt, one decision per pending tool call, in order:
        `{"type": "approve"}`, `{"type": "edit", "edited_action": {"name": ..., "args": ...}}`,
        `{"type": "reject", "message": "..."}`."""
        rejected = any(d.get("type") == "reject" for d in decisions)
        return await self._run(task, Command(resume={"decisions": list(decisions)}), self._masker(task), rejected)

    async def _run(self, task: Task, payload: Any, masker: Masker, rejected: bool) -> Outcome:
        graph, prompt = self._agent(task.task_type)
        ctx = HarnessContext(task=task, sink=self.sink, masker=masker, clock=self.clock)
        config = run_config(
            task,
            prompt_version=prompt.version,
            model=_model_name(self.model),
            recursion_limit=self.recursion_limit,
        )
        run_id = str(config["run_id"])
        try:
            with tracing_context(project_name=self.project, client=self.trace_client):
                await self._stream(graph, payload, config, ctx)
            state = await graph.aget_state({"configurable": {"thread_id": task.thread_id}})
        except HarnessFailure as failure:
            return self._failed(ctx, run_id, failure.code, failure.message)
        except GraphRecursionError:
            return self._failed(ctx, run_id, FailureCode.LOOP_MAX_TURNS, "그래프 단계 한도 초과")
        except Exception as exc:  # noqa: BLE001 - every task must end with an event the backend can retry on
            log.exception("task %s failed", task.task_id)
            return self._failed(ctx, run_id, FailureCode.INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")

        if state.interrupts:
            approvals = [
                {
                    "tool": action["name"],
                    "args": action["args"],
                    "approver": self.config.guardrails.approver.get(action["name"], "manager"),
                    "description": action.get("description", ""),
                }
                for interrupt in state.interrupts
                for action in interrupt.value["action_requests"]
            ]
            self._emit(ctx, "APPROVAL_REQUIRED", {"approvals": approvals, "run_id": run_id})
            return Outcome("APPROVAL_REQUIRED", task.task_id, task.trace_id, run_id, approvals=approvals, stats=ctx.stats)

        last = next((m for m in reversed(state.values["messages"]) if isinstance(m, AIMessage)), None)
        answer = masker.text(last.text if last else "")
        self._emit(ctx, "DONE", {"answer": answer, "rejected": rejected, **self._summary(ctx, run_id)})
        return Outcome("DONE", task.task_id, task.trace_id, run_id, answer=answer, rejected=rejected, stats=ctx.stats)

    async def _stream(self, graph: Any, payload: Any, config: dict, ctx: HarnessContext) -> None:
        async for message, meta in graph.astream(payload, config, context=ctx, stream_mode="messages"):
            # Streaming models yield AIMessageChunk (a subclass); non-streaming ones yield one full AIMessage.
            if meta.get("langgraph_node") == "model" and isinstance(message, AIMessage) and message.text:
                self._emit(ctx, "TOKEN", {"text": ctx.masker.text(message.text)})

    def _summary(self, ctx: HarnessContext, run_id: str) -> dict[str, Any]:
        s = ctx.stats
        return {
            "run_id": run_id,
            "langsmith_project": self.project,
            "fallback": s.fallback,
            "turns": s.turns,
            "tool_calls": s.tool_calls,
            "retries": s.retries,
            "truncated": s.truncated,
            "input_tokens": s.input_tokens,
            "output_tokens": s.output_tokens,
            "elapsed_s": round(ctx.elapsed(), 3),
        }

    def _failed(self, ctx: HarnessContext, run_id: str, code: FailureCode, message: str) -> Outcome:
        message = ctx.masker.text(message)
        self._emit(ctx, "FAILED", {"code": str(code), "message": message, **self._summary(ctx, run_id)})
        t = ctx.task
        return Outcome("FAILED", t.task_id, t.trace_id, run_id, code=str(code), message=message, stats=ctx.stats)

    def _emit(self, ctx: HarnessContext, type_: str, data: dict[str, Any]) -> None:
        ctx.sink.emit(ProgressEvent(type_, ctx.task.task_id, ctx.task.trace_id, data))


async def connect(
    config: HarnessConfig, servers: dict[str, str], sink: EventSink, **kwargs: Any
) -> HarnessAgent:
    """Production wiring: tools from the MCP servers over Streamable HTTP."""
    from .tools import mcp_client

    tools = await mcp_client(servers, config.observability.trace_header).get_tools()
    return HarnessAgent(config, tools, sink, **kwargs)
