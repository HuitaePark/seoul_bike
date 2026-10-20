# Author: Cheon Seonghun
# Date: 20OCT2026
# Purpose: HarnessAgent: assembles the 8 Harness elements around the model and runs one task segment.

"""Entry point the agent server calls for every `ai.tasks` message."""
from __future__ import annotations

import asyncio
import itertools
import logging
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
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
from .contract import ApprovalMismatch, approval_card, decision_from_resume, sources
from .errors import FailureCode, HarnessFailure
from .events import EventSink
from .guardrails import Masker, approval_middleware, destructive_tools
from .loop import LoopGuardMiddleware
from .prompts import Prompt, load_prompt, task_message
from .recovery import RecoveryMiddleware, Sleep
from .task import HarnessContext, Outcome, Task
from .tools import ToolResultMiddleware, hide_injected_args, select_tools
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
        self.tools = hide_injected_args(tools, config.guardrails.injected_args)
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
        self._seq: dict[str, itertools.count] = {}
        # One masker per process (aliases are name hashes); also the LangSmith anonymizer (tracing.langsmith_client).
        self.masker = Masker(config.guardrails.mask_keys)
        self.trace_client = langsmith_client(self.masker) if tracing_is_enabled() else None

    @property
    def recursion_limit(self) -> int:
        # Backstop only: LoopGuard stops first. One turn is at most ~6 graph steps (hooks, model, tools).
        return self.config.loop.max_turns * 6 + 10

    def _agent(self, task_type: str) -> tuple[Any, Prompt]:
        if task_type not in self._built:
            cfg = self.config
            tools = select_tools(self.tools, task_type, cfg.tools)
            gated = destructive_tools(tools)
            if missing := [name for name in gated if name not in cfg.guardrails.approval]:
                # Every destructive tool must have an approval card; never run one ungated.
                raise ValueError(f"guardrails.approval has no rule for destructive tools {missing}")
            prompt = load_prompt(cfg.prompts_dir, task_type)
            middleware = [
                LoopGuardMiddleware(cfg.loop),
                ArgumentValidationMiddleware({t.name for t in tools}, cfg.guardrails.id_pattern, gated),
                UsageMiddleware(),
                StateBriefMiddleware(),
                RecoveryMiddleware(cfg.recovery, self.fallback_model, self.sleep),
                ToolResultMiddleware(cfg.tools, cfg.observability.tool_labels, gated, cfg.guardrails.injected_args),
                summarization(self.model, cfg.context),
                approval_middleware(gated, cfg.guardrails.approval),
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

    def _next_seq(self, task: Task) -> Callable[[], int]:
        counter = self._seq.setdefault(task.task_id, itertools.count(1))
        return lambda: next(counter)

    async def _state(self, task: Task) -> Any:
        graph, _ = self._agent(task.task_type)
        return await graph.aget_state({"configurable": {"thread_id": task.thread_id}})

    async def history(self, task: Task) -> list[BaseMessage]:
        """Masked message history of the task's thread (what the model saw), for evaluation and debugging."""
        return list((await self._state(task)).values.get("messages", []))

    async def pending_approval(self, task: Task) -> dict[str, Any] | None:
        """The approval card (contract `ApprovalRequest`) the task is waiting on, rebuilt from the checkpoint."""
        state = await self._state(task)
        if not state.interrupts:
            return None
        messages = state.values["messages"]
        ai = next(m for m in reversed(messages) if isinstance(m, AIMessage))
        call = next(tc for tc in ai.tool_calls if tc["name"] in self.config.guardrails.approval)
        return approval_card(task, call, messages, self.config.guardrails.approval)

    async def start(self, task: Task) -> Outcome:
        """New task: requester and report values are written by code; free text is masked and wrapped as
        untrusted user input."""
        masker = self._masker(task)
        payload = {"messages": [HumanMessage(task_message(task, masker.text(task.input)))]}
        return await self._run(task, payload, masker, rejected=False)

    async def resume_command(self, task: Task, resume: Mapping[str, Any]) -> Outcome:
        """Contract RESUME (`TaskCommand.resume`): checks it answers the pending card and comes from that card's
        approver, then resumes. Raises ApprovalMismatch without running anything otherwise."""
        card = await self.pending_approval(task)
        if card is None:
            raise ApprovalMismatch(f"task {task.task_id} is not waiting for approval")
        return await self.resume(task, [decision_from_resume(resume, card)])

    async def resume(self, task: Task, decisions: Sequence[dict[str, Any]]) -> Outcome:
        """Low-level resume with Harness decisions: `{"type": "approve"}`, `{"type": "reject", "message": ...}`,
        `{"type": "edit", "edited_action": {"name": ..., "args": ...}}`. Prefer `resume_command`."""
        rejected = any(d.get("type") == "reject" for d in decisions)
        return await self._run(task, Command(resume={"decisions": list(decisions)}), self._masker(task), rejected)

    async def _run(self, task: Task, payload: Any, masker: Masker, rejected: bool) -> Outcome:
        graph, prompt = self._agent(task.task_type)
        ctx = HarnessContext(task=task, sink=self.sink, masker=masker, next_seq=self._next_seq(task), clock=self.clock)
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
            card = await self.pending_approval(task)
            ctx.emit("APPROVAL_REQUIRED", {"approval": card}, {"runId": run_id})
            return Outcome("APPROVAL_REQUIRED", task.task_id, task.trace_id, run_id, approval=card, stats=ctx.stats)

        messages = state.values["messages"]
        last = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
        answer = masker.text(last.text if last else "")
        used = sources(messages, self.config.observability.tool_labels)
        ctx.emit("DONE", {"answer": answer, "sources": used}, {"rejected": rejected, **self._summary(ctx, run_id)})
        return Outcome("DONE", task.task_id, task.trace_id, run_id, answer=answer, rejected=rejected, stats=ctx.stats)

    async def _stream(self, graph: Any, payload: Any, config: dict, ctx: HarnessContext) -> None:
        async for message, meta in graph.astream(payload, config, context=ctx, stream_mode="messages"):
            # Streaming models yield AIMessageChunk (a subclass); non-streaming ones yield one full AIMessage.
            if meta.get("langgraph_node") == "model" and isinstance(message, AIMessage) and message.text:
                ctx.emit("TOKEN", {"token": ctx.masker.text(message.text)})

    def _summary(self, ctx: HarnessContext, run_id: str) -> dict[str, Any]:
        """Extension fields (`harness`) for DONE and FAILED, camelCase like the contract."""
        s = ctx.stats
        return {
            "runId": run_id,
            "langsmithProject": self.project,
            "fallback": s.fallback,
            "turns": s.turns,
            "toolCalls": s.tool_calls,
            "retries": s.retries,
            "truncated": s.truncated,
            "inputTokens": s.input_tokens,
            "outputTokens": s.output_tokens,
            "elapsedS": round(ctx.elapsed(), 3),
        }

    def _failed(self, ctx: HarnessContext, run_id: str, code: FailureCode, message: str) -> Outcome:
        message = ctx.masker.text(message)
        ctx.emit("FAILED", {"error": message}, {"code": str(code), **self._summary(ctx, run_id)})
        t = ctx.task
        return Outcome("FAILED", t.task_id, t.trace_id, run_id, code=str(code), message=message, stats=ctx.stats)


async def connect(
    config: HarnessConfig, servers: dict[str, str], sink: EventSink, **kwargs: Any
) -> HarnessAgent:
    """Production wiring: tools from the MCP servers over Streamable HTTP."""
    from .tools import mcp_client

    tools = await mcp_client(servers, config.observability.trace_header).get_tools()
    return HarnessAgent(config, tools, sink, **kwargs)
