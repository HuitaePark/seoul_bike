# Author: Cheon Seonghun
# Date: 20OCT2026
# Purpose: HarnessAgent: assembles the 8 Harness elements around the model and runs one task segment.

"""Entry point the agent server calls for every `ai.tasks` message."""
from __future__ import annotations

import asyncio
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
from .context import ApprovedChangeNoteMiddleware, StateBriefMiddleware, summarization
from .contract import ApprovalMismatch, approval_card, approvers, decision_from_resume, sources
from .errors import FailureCode, HarnessFailure
from .events import EventSink
from .guardrails import Masker, approval_middleware, destructive_tools
from .loop import LoopGuardMiddleware
from .prompts import Prompt, load_prompt, task_message
from .recovery import RecoveryMiddleware, Sleep, model_name
from .task import HarnessContext, Outcome, Task
from .tools import ToolResultMiddleware, hide_injected_args, offered_tools
from .trace import DecisionTraceMiddleware, NullTrace, TraceSink, clip, with_rationale
from .tracing import UsageMiddleware, langsmith_client, run_config
from .validation import ArgumentValidationMiddleware

log = logging.getLogger(__name__)


class HarnessAgent:
    """Model + Harness for one agent server process.

    Middleware order (outer → inner) for model calls: LoopGuard → ArgumentValidation → DecisionTrace → Usage →
    StateBrief → ApprovedChangeNote → Recovery. Tool calls: LoopGuard → ToolResult. Hooks: Summarization before the model, approval
    interrupt after it.

    `tracer` receives the decision trace (harness/trace.py). Without one, a sink that can also trace (the graph's
    ShiftEventSink, ListSink in tests) is used, else nothing is recorded.
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
        tracer: TraceSink | None = None,
    ) -> None:
        self.config = config
        tools = list(tools)
        # Tools whose MCP schema takes `task_id` (change tools and request_route_plan) get it from Harness.
        self.task_id_tools = {
            t.name for t in tools if isinstance(t.args_schema, dict) and "task_id" in t.args_schema.get("properties", {})
        }
        # The model sees no Harness-filled ids and must give a `rationale` for every call (stripped before running).
        self.tools = with_rationale(hide_injected_args(tools, config.guardrails.injected_args))
        self.sink = sink
        self.tracer = tracer or (sink if isinstance(sink, TraceSink) else NullTrace())
        kwargs = config.models.kwargs
        self.model = model or init_chat_model(config.models.primary, **kwargs)
        self.fallback_model = fallback_model or (
            init_chat_model(config.models.fallback, **kwargs) if config.models.fallback else None
        )
        self.checkpointer = checkpointer or InMemorySaver()
        self.project = project or config.observability.project
        self.clock = clock
        self.sleep = sleep
        self._built: dict[tuple[str, str], tuple[Any, Prompt]] = {}  # (task_type, requester role) -> graph
        self._offered: dict[tuple[str, str], tuple[list[str], list[dict[str, str]]]] = {}  # same key -> tools, withheld
        self._steps: dict[str, int] = {}  # task_id -> card step in the approval order (in memory, docs/ISSUES.md ISS-19)
        # One masker per process (aliases are name hashes); also the LangSmith anonymizer (tracing.langsmith_client).
        self.masker = Masker(config.guardrails.mask_keys)
        self.trace_client = langsmith_client(self.masker) if tracing_is_enabled() else None

    @property
    def recursion_limit(self) -> int:
        # Backstop only: LoopGuard stops first. One turn is at most ~6 graph steps (hooks, model, tools).
        return self.config.loop.max_turns * 6 + 10

    def _agent(self, task: Task) -> tuple[Any, Prompt]:
        # The tool list depends on the requester's role too (tools.driver_deny), so the role is part of the key.
        key = (task.task_type, task.requester.role)
        if key not in self._built:
            cfg = self.config
            tools, withheld = offered_tools(self.tools, task.task_type, task.requester.role, cfg.tools)
            gated = destructive_tools(tools)
            if missing := [name for name in gated if name not in cfg.guardrails.approval]:
                # Every destructive tool must have an approval card; never run one ungated.
                raise ValueError(f"guardrails.approval has no rule for destructive tools {missing}")
            prompt = load_prompt(cfg.prompts_dir, task.task_type)
            preview = cfg.observability.trace_preview_chars
            middleware = [
                LoopGuardMiddleware(cfg.loop),
                ArgumentValidationMiddleware({t.name for t in tools}, cfg.guardrails.id_pattern, gated),
                DecisionTraceMiddleware(preview),
                UsageMiddleware(),
                StateBriefMiddleware(),
                ApprovedChangeNoteMiddleware(gated),
                RecoveryMiddleware(cfg.recovery, self.fallback_model, self.sleep),
                ToolResultMiddleware(
                    cfg.tools, cfg.observability.tool_labels, cfg.guardrails.approval, cfg.guardrails.injected_args,
                    self.task_id_tools, preview,
                ),
                summarization(self.model, cfg.context),
                approval_middleware(gated, cfg.guardrails.approval),
            ]
            self._offered[key] = ([t.name for t in tools], withheld)
            graph = create_agent(
                self.model,
                tools,
                system_prompt=prompt.text,
                middleware=middleware,
                context_schema=HarnessContext,
                checkpointer=self.checkpointer,
                name=f"harness_{task.task_type}",
            )
            self._built[key] = (graph, prompt)
        return self._built[key]

    def _masker(self, task: Task) -> Masker:
        self.masker.add_names(task.driver_names)
        return self.masker

    async def _state(self, task: Task) -> Any:
        graph, _ = self._agent(task)
        return await graph.aget_state({"configurable": {"thread_id": task.thread_id}})

    async def history(self, task: Task) -> list[BaseMessage]:
        """Masked message history of the task's thread (what the model saw), for evaluation and debugging."""
        return list((await self._state(task)).values.get("messages", []))

    async def _pending_call(self, task: Task) -> tuple[dict[str, Any], list[BaseMessage]] | None:
        state = await self._state(task)
        if not state.interrupts:
            return None
        messages = state.values["messages"]
        ai = next(m for m in reversed(messages) if isinstance(m, AIMessage))
        return next(tc for tc in ai.tool_calls if tc["name"] in self.config.guardrails.approval), messages

    async def pending_approval(self, task: Task) -> dict[str, Any] | None:
        """The approval card (contract `ApprovalRequest`) the task is waiting on, rebuilt from the checkpoint and
        the card step (0 = first approver; a major route change has a second card for the driver)."""
        pending = await self._pending_call(task)
        if pending is None:
            return None
        call, messages = pending
        rules = self.config.guardrails.approval
        return approval_card(task, call, messages, rules, self._steps.get(task.task_id, 0))

    async def start(self, task: Task) -> Outcome:
        """New task: requester and report values are written by code; free text is masked and wrapped as
        untrusted user input."""
        masker = self._masker(task)
        payload = {"messages": [HumanMessage(task_message(task, masker.text(task.input)))]}
        return await self._run(task, payload, masker, rejected=False)

    async def resume_command(self, task: Task, resume: Mapping[str, Any]) -> Outcome:
        """Contract RESUME (`TaskCommand.resume`). Checks it answers the pending card and comes from that card's
        approver; raises ApprovalMismatch without running anything otherwise.

        An approval that leaves approvers in the order (a major route change approved by the manager) does not run
        the change: the next card (driver acceptance) is sent instead. Rejection at any card ends the sequence."""
        pending = await self._pending_call(task)
        if pending is None:
            raise ApprovalMismatch(f"task {task.task_id} is not waiting for approval")
        call, messages = pending
        rules = self.config.guardrails.approval
        step = self._steps.get(task.task_id, 0)
        card = approval_card(task, call, messages, rules, step)
        decision = decision_from_resume(resume, card)
        if decision["type"] == "approve" and step + 1 < len(approvers(call, messages, rules)):
            self._steps[task.task_id] = step + 1
            following = approval_card(task, call, messages, rules, step + 1)
            ctx = HarnessContext(task=task, sink=self.sink, masker=self._masker(task), clock=self.clock,
                                 tracer=self.tracer)
            ctx.emit("APPROVAL_REQUIRED", {"approval": following})
            self._trace_wait(ctx, following, "앞 카드가 승인되어 같은 변경의 다음 카드를 보낸다 (변경은 아직 실행하지 않는다)")
            return Outcome("APPROVAL_REQUIRED", task.task_id, task.trace_id, "", approval=following, stats=ctx.stats)
        self._steps.pop(task.task_id, None)
        return await self.resume(task, [decision])

    async def resume(self, task: Task, decisions: Sequence[dict[str, Any]]) -> Outcome:
        """Low-level resume with Harness decisions: `{"type": "approve"}`, `{"type": "reject", "message": ...}`,
        `{"type": "edit", "edited_action": {"name": ..., "args": ...}}`. Prefer `resume_command`."""
        rejected = any(d.get("type") == "reject" for d in decisions)
        return await self._run(task, Command(resume={"decisions": list(decisions)}), self._masker(task), rejected,
                               decisions=decisions)

    async def _run(self, task: Task, payload: Any, masker: Masker, rejected: bool,
                   decisions: Sequence[dict[str, Any]] | None = None) -> Outcome:
        graph, prompt = self._agent(task)
        config = run_config(
            task,
            prompt_version=prompt.version,
            model=model_name(self.model),
            recursion_limit=self.recursion_limit,
        )
        run_id = str(config["run_id"])
        ctx = HarnessContext(task=task, sink=self.sink, masker=masker, clock=self.clock, tracer=self.tracer,
                             run_id=run_id)
        self._trace_start(ctx, prompt, decisions)
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
            self._trace_wait(ctx, card, "변경 도구라 실행 전에 멈추고 승인 카드를 보낸다")
            self._trace_end(ctx, run_id, "APPROVAL_REQUIRED", "승인 대기로 멈춤")
            return Outcome("APPROVAL_REQUIRED", task.task_id, task.trace_id, run_id, approval=card, stats=ctx.stats)

        messages = state.values["messages"]
        last = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
        answer = masker.text(last.text if last else "")
        used = sources(messages, self.config.observability.tool_labels)
        ctx.emit("DONE", {"answer": answer, "sources": used}, {"rejected": rejected, **self._summary(ctx, run_id)})
        self._trace_end(ctx, run_id, "DONE", "완료", answer=answer, rejected=rejected,
                        sources=[s["title"] for s in used])
        return Outcome("DONE", task.task_id, task.trace_id, run_id, answer=answer, rejected=rejected, stats=ctx.stats)

    def _trace_start(self, ctx: HarnessContext, prompt: Prompt, decisions: Sequence[dict[str, Any]] | None) -> None:
        """RUN_START: what the model is given to choose from, and why some tools are not offered."""
        task, cfg = ctx.task, self.config
        tools, withheld = self._offered[(task.task_type, task.requester.role)]
        detail: dict[str, Any] = {
            "segment": "resume" if decisions is not None else "start",
            "taskType": task.task_type,
            "requester": {"role": task.requester.role, "truckId": task.requester.truck_id},
            "model": model_name(self.model),
            "fallbackModel": model_name(self.fallback_model) if self.fallback_model else None,
            "promptVersion": prompt.version,
            "tools": tools,
            "withheld": withheld,
            "limits": {"maxTurns": cfg.loop.max_turns, "maxSeconds": cfg.loop.seconds_for(task.task_type),
                       "maxSameToolCalls": cfg.loop.max_same_tool_calls},
        }
        preview = cfg.observability.trace_preview_chars
        if decisions is None:
            detail["input"] = clip(ctx.masker.text(task.input), preview)
            if task.report:
                detail["report"] = task.report.render()
            title = f"모델 경로 시작 · {task.task_type} · 도구 {len(tools)}개 중에서 모델이 고른다"
        else:
            detail["decisions"] = [{"type": d.get("type"), "message": clip(d.get("message"), preview) or None}
                                   for d in decisions]
            title = f"승인 결정({', '.join(str(d.get('type')) for d in decisions)})을 받아 멈춘 호출부터 이어 간다"
        ctx.trace("RUN_START", title, detail)

    def _trace_wait(self, ctx: HarnessContext, card: dict[str, Any], why: str) -> None:
        order = card.get("requiredApprovers") or [card["approver"]]
        step = order.index(card["approver"]) + 1 if card["approver"] in order else 1
        ctx.trace("APPROVAL_WAIT", f"승인 대기 ({card['approver']} {step}/{len(order)}): {card.get('summary')}", {
            "approvalId": card["approvalId"], "kind": card["kind"], "approver": card["approver"],
            "summary": card.get("summary"), "step": step, "of": len(order), "why": why,
        }, turn=ctx.stats.turns or None)

    def _trace_end(self, ctx: HarnessContext, run_id: str, status: str, label: str, **detail: Any) -> None:
        stats = self._summary(ctx, run_id)
        title = f"{label} · {stats['turns']}턴, 도구 {stats['toolCalls']}회, {stats['elapsedS']:.2f}초"
        if detail.get("answer") is not None:
            detail["answer"] = clip(detail["answer"], self.config.observability.trace_preview_chars)
        ctx.trace("RUN_END", title, {"status": status, **detail, "stats": stats})

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
        ctx.emit("FAILED", {"error": message, "code": str(code)}, self._summary(ctx, run_id))
        self._trace_end(ctx, run_id, "FAILED", f"실패 {code}: {clip(message, 120)}", code=str(code), message=message)
        t = ctx.task
        return Outcome("FAILED", t.task_id, t.trace_id, run_id, code=str(code), message=message, stats=ctx.stats)


async def connect(
    config: HarnessConfig, servers: dict[str, str], sink: EventSink, **kwargs: Any
) -> HarnessAgent:
    """Production wiring: tools from the MCP servers over Streamable HTTP."""
    from .tools import mcp_client

    tools = await mcp_client(servers, config.observability.trace_header).get_tools()
    return HarnessAgent(config, tools, sink, **kwargs)
