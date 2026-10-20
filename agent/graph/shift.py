# Author: Jung Minkyu
# Date: 20OCT2026
# Purpose: 근무조 그래프. TaskCommand 1건을 받아 Harness(또는 LLM 없는 경로)로 처리하고, 대기 카드와 근무조 상태를 남긴다.

"""근무조 그래프 (docs/AGENT-GRAPH.md 2절).

    START → receive ─┬─ harness → run_harness ─┐
                     ├─ code    → run_code ────┼→ settle ─┬─ end  → END
                     ├─ settle ────────────────┘          └─ code → run_code (모델 장애 → LLM 없는 경로)
                     └─ skip    → END (중복 명령, 대기 중이 아닌 승인, 승인 주체가 아닌 결정)

맡는 일 (HARNESS v0.5 1.5): 카드·승인 순서·계약 이벤트는 Harness가 만든다. 그래프는 작업 접수와 보고 읽기,
`task_from_command`·`resume_command` 호출, 대기 카드 표(작업 사이 대체, 재시작 뒤 카드 단계 ISS-19),
`brief`, LLM 없는 경로를 맡는다.

스레드 두 층
- 작업 스레드 `{shiftId}:{taskId}` (= 명령의 `threadId`, asyncapi v0.3): Harness. 모델 대화와 변경 도구 앞 interrupt
- 근무조 기록 `shiftId`: 이 그래프의 내부 기록. 작업 기록·대기 카드·최근 보고를 둔다. interrupt가 없어 작업끼리 대기 승인이
  섞이지 않는다. 계획의 원본은 backend DB이고 여기 값은 brief·대체 판단용 사본이다
근무조 그래프에는 interrupt가 없다. 그래서 계획이 승인을 기다려도 기사 보고는 바로 처리된다 (PRD 6.1-4).
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
from collections import defaultdict
from collections.abc import Callable, Coroutine, Iterable
from typing import Annotated, Any, TypedDict

from langchain_core.runnables.config import var_child_runnable_config
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from harness import EventSink, FieldReport, HarnessAgent, HarnessConfig, Task, load_config
from harness.contract import ApprovalMismatch, task_from_command
from harness.guardrails import Masker

from .approvals import (
    card_step,
    clean_resume,
    doc_sources,
    enrich_card,
    exclusions,
    merge_sources,
    same_target,
    tool_results,
)
from .brief import make_brief
from .codepath import CodePath, ToolFailed, ToolMissing, failed
from .contract import CommandError, TaskCommand
from .events import Publish, ShiftEventSink

log = logging.getLogger(__name__)

MODEL_FAILURES = {"MODEL_UNAVAILABLE", "MODEL_ERROR"}  # 이 실패면 LLM 없는 경로로 다시 해 본다
DEFAULT_INPUT = {"PLAN_DRAFT": "이번 근무조의 재배치 계획 초안을 만들어 주세요."}


def merge(left: dict | None, right: dict | None) -> dict:
    """딕셔너리 상태 갱신: 바뀐 키만 덮어쓴다."""
    return {**(left or {}), **(right or {})}


class ShiftState(TypedDict, total=False):
    """근무조 스레드 상태. 체크포인트에 그대로 저장되므로 JSON으로 표현되는 값만 둔다."""

    shift_id: str
    command: dict  # 이번 실행의 TaskCommand 원문
    current: str  # 이번 실행이 다루는 taskId
    route: str  # 다음 노드 선택
    outcome: dict  # 이번 실행 결과
    tasks: Annotated[dict[str, dict], merge]  # taskId → 작업 기록
    approvals: Annotated[dict[str, dict], merge]  # approvalId → 카드와 상태 (PENDING·DECIDED·SUPERSEDED)
    plan: dict  # {"active": planId, "proposed": planId}
    reports: list[dict]  # 최근 현장 보고 (최대 10건)


def _requester_problem(cmd: TaskCommand, report: dict | None) -> str | None:
    """기사는 트럭이 있어야 한다: 다른 트럭 정보를 보지 않게 (NFR-06). `requestedBy.truckId`(asyncapi v0.3), 없으면 보고의 트럭."""
    if cmd.requested_by["role"] == "DRIVER" and not (cmd.requested_by.get("truckId") or (report or {}).get("truckId")):
        return "기사 작업인데 트럭을 알 수 없다 (requestedBy.truckId 또는 보고의 truckId 필요)"
    return None


class ShiftAgent:
    """agent 서버의 바깥 껍질. 근무조마다 명령을 한 번에 하나씩 그래프에 넣는다.

    harness: EventSink를 받아 HarnessAgent를 만드는 함수. None이면 모든 작업을 LLM 없는 경로로 처리한다.
    driver_names: 자유 글(의견·메모)에서 가릴 기사 이름. 계약에 이름 목록이 없어 설정으로 받는다.
    """

    def __init__(
        self,
        tools: Iterable[BaseTool],
        publish: Publish,
        *,
        config: HarnessConfig | None = None,
        harness: Callable[[EventSink], HarnessAgent] | None = None,
        checkpointer: BaseCheckpointSaver | None = None,
        driver_names: Iterable[str] = (),
    ) -> None:
        self.config = config or load_config()
        self.sink = ShiftEventSink(publish)
        self.code = CodePath(tools, self.config)
        self.agent = harness(self.sink) if harness else None
        self.checkpointer = checkpointer or InMemorySaver()
        self.driver_names = tuple(driver_names)
        self.masker = Masker(self.config.guardrails.mask_keys, self.driver_names)
        self.graph = self._build()
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    # ---- 바깥에서 부르는 것 ----

    async def handle(self, command: dict) -> dict:
        """TaskCommand 1건을 처리하고 이번 실행 결과를 돌려준다. 계약 위반이면 CommandError."""
        cmd = TaskCommand.parse(command)
        async with self._locks[cmd.shift_id]:  # 같은 근무조 명령은 순서대로 (Kafka 키 = shiftId)
            state = await self.graph.ainvoke(
                {"shift_id": cmd.shift_id, "command": command, "current": "", "route": "", "outcome": {}},
                {"configurable": {"thread_id": cmd.shift_id}},
            )
        return state["outcome"]

    async def state(self, shift_id: str) -> dict:
        """근무조 상태 (화면·테스트용)."""
        snapshot = await self.graph.aget_state({"configurable": {"thread_id": shift_id}})
        return dict(snapshot.values)

    # ---- 그래프 ----

    def _build(self):
        g = StateGraph(ShiftState)
        g.add_node("receive", self._receive)
        g.add_node("run_harness", self._run_harness)
        g.add_node("run_code", self._run_code)
        g.add_node("settle", self._settle)
        g.add_edge(START, "receive")
        g.add_conditional_edges(
            "receive", lambda s: s["route"],
            {"harness": "run_harness", "code": "run_code", "settle": "settle", "skip": END},
        )
        g.add_edge("run_harness", "settle")
        g.add_edge("run_code", "settle")
        g.add_conditional_edges("settle", lambda s: s["route"], {"code": "run_code", "end": END})
        return g.compile(checkpointer=self.checkpointer, name="shift")

    async def _receive(self, state: ShiftState) -> dict:
        """명령 검증, 작업 등록, 경로 선택."""
        cmd = TaskCommand.parse(state["command"])
        tasks, approvals = state.get("tasks") or {}, state.get("approvals") or {}

        if cmd.type == "RESUME":
            r = cmd.resume
            pending = approvals.get(r.approval_id)
            if not pending or pending["status"] != "PENDING":
                # backend가 409로 먼저 막지만, 늦게 온 결정이 다른 계획을 실행하지 않도록 한 번 더 막는다
                log.info("대기 중이 아닌 승인 %s (%s)", r.approval_id, pending and pending["status"])
                return {"route": "skip", "outcome": {"status": "IGNORED", "taskId": cmd.task_id,
                                                     "reason": "대기 중인 승인이 아니다"}}
            card = pending["card"]
            if r.decided_by.get("role") != card["approver"]:
                # backend가 403(NOT_APPROVER)으로 먼저 막는다. 그래도 다른 주체의 결정으로 변경을 실행하지 않는다
                log.warning("승인 주체가 아닌 결정 %s: 카드 %s, 결정 %s", r.approval_id, card["approver"],
                            r.decided_by.get("role"))
                return {"route": "skip", "outcome": {"status": "IGNORED", "taskId": cmd.task_id,
                                                     "reason": "승인 주체가 아니다"}}
            record = tasks[pending["taskId"]]
            # 모델 없이 다시 뜬 서버라면 LLM 경로로 시작한 작업도 기다리던 도구 호출을 코드가 이어서 실행할 수 없다.
            # Harness의 멈춘 호출은 Harness만 재개할 수 있으므로 그대로 Harness 경로로 보낸다.
            exclude = exclusions(card, r.exclude_station_ids)
            resume = {"approvalId": r.approval_id, "decision": r.decision, "decidedBy": r.decided_by}
            if exclude:
                resume["excludeStationIds"] = exclude
            if r.comment:
                resume["comment"] = r.comment
            return {
                "current": record["taskId"],
                "route": record["path"],
                "approvals": {r.approval_id: {**pending, "status": "DECIDED", "decision": r.decision}},
                "tasks": {record["taskId"]: {**record, "status": "RUNNING", "resume": resume,
                                             "excluded": [*(record.get("excluded") or []), *exclude]}},
            }

        old = tasks.get(cmd.task_id)
        if old and old["status"] != "FAILED":
            return {"route": "skip", "outcome": {"status": "IGNORED", "taskId": cmd.task_id, "reason": "이미 받은 작업"}}

        report = None
        if cmd.type == "FIELD_REPORT":
            report = cmd.report or await self._quiet(self.code.find_report(cmd.shift_id, cmd.report_id))
            if report and not report.get("truckId"):
                report = {**report, "truckId": cmd.requested_by.get("truckId")}
        problem = _requester_problem(cmd, report)
        if cmd.type == "FIELD_REPORT" and not report:
            problem = f"보고 {cmd.report_id}를 찾지 못했다"
        record = {
            "taskId": cmd.task_id,
            "type": cmd.type,
            "shiftId": cmd.shift_id,
            "traceId": cmd.trace_id,
            "command": state["command"],
            "report": report,
            "attempt": (old or {}).get("attempt", 0) + 1,
            "status": "RUNNING",
            "path": self._path(cmd, report),
            "resume": None,
            "excluded": [],
            "pending": None,
            "pendingApproval": None,
        }
        update: dict[str, Any] = {"current": cmd.task_id, "route": record["path"], "tasks": {cmd.task_id: record}}
        if report:
            update["reports"] = [*(state.get("reports") or []), report][-10:]
        if problem:
            update["route"] = "settle"
            update["outcome"] = failed("OUTPUT_INVALID", problem)
        return update

    def _path(self, cmd: TaskCommand, report: dict | None) -> str:
        """LLM 경로(harness)인가, LLM 없는 경로(code)인가."""
        if self.agent is None:
            return "code"
        if cmd.type == "FIELD_REPORT" and report is not None and not report.get("memo"):
            return "code"  # 메모 없는 보고는 숫자뿐이다 (HARNESS 10절)
        return "harness"

    async def _run_harness(self, state: ShiftState) -> dict:
        """Harness `start` 또는 `resume_command`. 잡아 둔 카드는 보강해 내보내고, 결과를 상태에 적는다."""
        record = state["tasks"][state["current"]]
        if self.agent is None:  # 모델 없이 다시 뜬 서버: Harness가 멈춘 호출은 이어서 실행할 수 없다
            return {"outcome": {**failed("MODEL_UNAVAILABLE", "모델 없이 다시 시작되어 승인 대기를 이어 갈 수 없다"),
                                "via": "graph"}}
        task = self._task(state, record)
        resume = record.get("resume")
        self.sink.hold(task.task_id)
        try:
            if resume:
                card = state["approvals"][resume["approvalId"]]["card"]
                step = card_step(card)
                if step:  # ISS-19: Harness는 카드 단계를 메모리에만 둔다. 재시작했으면 체크포인트의 단계로 되돌린다
                    self.agent._steps.setdefault(task.task_id, step)
                outcome = await self._outside_graph(
                    self.agent.resume_command(task, clean_resume(resume, card, self.masker.text)))
            else:
                outcome = await self._outside_graph(self.agent.start(task))
        except ApprovalMismatch as exc:
            self.sink.release(task.task_id)
            return {"outcome": {"status": "IGNORED", "reason": str(exc), "via": "harness"}}
        history = await self._outside_graph(self.agent.history(task))
        results, excluded = tool_results(history), record.get("excluded") or []
        cards: dict[str, dict] = {}

        def enrich(card: dict) -> dict:
            cards[card["approvalId"]] = enrich_card(card, results, excluded)
            return cards[card["approvalId"]]

        docs = doc_sources(history)
        self.sink.release_with(task.task_id, enrich, lambda sources: merge_sources(sources, docs))
        result: dict[str, Any] = {
            "status": outcome.status, "answer": outcome.answer, "code": outcome.code,
            "message": outcome.message, "rejected": outcome.rejected, "via": "harness",
        }
        if outcome.approval:
            result["approval"] = cards.get(outcome.approval["approvalId"]) or enrich(outcome.approval)
        return {"outcome": result}

    async def _run_code(self, state: ShiftState) -> dict:
        """LLM 없는 경로 1구간."""
        record = state["tasks"][state["current"]]
        try:
            task = self._task(state, record)
            outcome = await self._outside_graph(self.code.run(record, task, self.sink, record.get("resume")))
        except ApprovalMismatch as exc:
            outcome = {"status": "IGNORED", "reason": str(exc)}
        except ToolMissing as exc:
            outcome = failed("TOOL_UNAVAILABLE", f"도구 없음: {exc}")
        except ToolFailed as exc:  # 승인된 변경이 backend에서 거부됨(403·409) 등. 실행했다고 답하지 않는다
            outcome = failed("TOOL_UNAVAILABLE", f"도구 오류로 끝냈다: {exc}")
        except Exception as exc:  # noqa: BLE001 - 작업은 항상 이벤트로 끝나야 backend가 재시도를 정한다
            log.exception("LLM 없는 경로 실패 %s", record["taskId"])
            outcome = failed("TOOL_UNAVAILABLE", f"{type(exc).__name__}: {exc}")
        return {"outcome": {**outcome, "via": "code"}}

    async def _settle(self, state: ShiftState) -> dict:
        """결과 정리: 대기 카드 표, 작업 사이 대체, 실패 이벤트, 모델 장애면 LLM 없는 경로로."""
        record = dict(state["tasks"][state["current"]])
        out = state["outcome"]
        approvals = state.get("approvals") or {}
        plan = dict(state.get("plan") or {})
        changed_approvals: dict[str, dict] = {}
        changed_tasks: dict[str, dict] = {}
        resume = record.get("resume")
        if "excluded" in out:
            record["excluded"] = out["excluded"]
        if "pending" in out:
            record["pending"] = out["pending"]

        if out["status"] == "IGNORED":
            # 결정이 대기 카드와 맞지 않아 아무것도 실행하지 않았다. 카드는 계속 기다린다
            log.warning("작업 %s: 결정 무시 (%s)", record["taskId"], out.get("reason"))
            if resume:
                changed_approvals[resume["approvalId"]] = {**approvals[resume["approvalId"]], "status": "PENDING"}
            record.update(status="WAITING_APPROVAL", resume=None)

        elif out["status"] == "APPROVAL_REQUIRED":
            card = out["approval"]
            for old_id, old in approvals.items():
                if old["status"] == "PENDING" and old["taskId"] != record["taskId"] and same_target(old["card"], card):
                    # 같은 대상의 새 카드: 다른 작업의 대기 카드는 대체한다 (늦은 승인이 낡은 계획을 실행하지 않게, G10)
                    changed_approvals[old_id] = {**old, "status": "SUPERSEDED", "supersededBy": card["approvalId"]}
                    old_task = {**state["tasks"][old["taskId"]], "status": "DONE", "pendingApproval": None}
                    self.sink.send(old_task, "DONE", answer=f"새 승인 요청으로 대체되어 실행하지 않았습니다: {old['card']['summary']}",
                                   sources=[])
                    changed_tasks[old_task["taskId"]] = old_task
            changed_approvals[card["approvalId"]] = {"approvalId": card["approvalId"], "taskId": record["taskId"],
                                                     "status": "PENDING", "card": card}
            record.update(status="WAITING_APPROVAL", pendingApproval=card["approvalId"], resume=None)
            if card["kind"] == "SUBMIT_PLAN":
                plan["proposed"] = card["planId"]

        elif out["status"] == "DONE":
            decided = approvals.get(resume["approvalId"]) if resume else None
            if decided and decided["card"]["kind"] == "SUBMIT_PLAN":
                if plan.get("proposed") == decided["card"]["planId"]:
                    plan["proposed"] = None
                if resume["decision"] == "APPROVE" and not resume.get("excludeStationIds") and not out.get("rejected"):
                    plan["active"] = decided["card"]["planId"]
            record.update(status="DONE", pendingApproval=None, resume=None)

        else:  # FAILED
            if self._can_fall_back(record, out):
                log.warning("모델 장애 %s → LLM 없는 경로 (%s)", record["taskId"], out.get("code"))
                self.sink.drop(record["taskId"])
                record["path"] = "code"
                return {"route": "code", "tasks": {record["taskId"]: record}}
            if out.get("via") != "harness":  # Harness는 FAILED를 이미 냈다 (잡아 둔 것을 아래에서 내보낸다)
                self.sink.send(record, "FAILED", error=f"{out.get('code')}: {out.get('message')}", code=out.get("code"))
            record.update(status="FAILED", pendingApproval=None, resume=None)

        self.sink.release(record["taskId"])  # 잡아 둔 이벤트가 남았으면 내보낸다
        changed_tasks[record["taskId"]] = record
        return {"route": "end", "tasks": changed_tasks, "approvals": changed_approvals, "plan": plan,
                "outcome": {**{k: v for k, v in out.items() if k not in {"pending", "via"}}, "taskId": record["taskId"],
                            "approvalId": record.get("pendingApproval")}}

    def _can_fall_back(self, record: dict, out: dict) -> bool:
        """시작 구간의 모델 장애만 대체한다. 재개 구간은 승인된 변경이 이미 실행됐을 수 있어서 다시 계산하지 않는다."""
        return (
            out.get("via") == "harness"
            and not record.get("resume")
            and out.get("code") in MODEL_FAILURES
            and record["type"] in {"PLAN_DRAFT", "FIELD_REPORT"}
        )

    # ---- 도우미 ----

    def _task(self, state: ShiftState, record: dict) -> Task:
        """작업 기록 → Harness `Task` (`task_from_command`). 기사 트럭은 requestedBy 또는 보고에서 읽는다."""
        command = dict(record["command"])
        if not command.get("text") and record["type"] in DEFAULT_INPUT:
            command["text"] = DEFAULT_INPUT[record["type"]]
        if record.get("report") and not command["requestedBy"].get("truckId") and command["requestedBy"]["role"] == "DRIVER":
            command["requestedBy"] = {**command["requestedBy"], "truckId": record["report"]["truckId"]}
        report = FieldReport.from_contract(record["report"]) if record.get("report") else None
        return task_from_command(command, report=report, brief=make_brief(state), driver_names=self.driver_names)

    async def _quiet(self, coro: Coroutine) -> Any:
        """부가 정보 읽기. 실패해도 작업을 막지 않는다."""
        try:
            return await self._outside_graph(coro)
        except Exception:  # noqa: BLE001
            log.warning("부가 조회 실패", exc_info=True)
            return None

    @staticmethod
    async def _outside_graph(coro: Coroutine) -> Any:
        """근무조 그래프 노드 안에서 Harness를 '독립 실행'으로 돌린다.

        노드 안에서 다른 LangGraph 그래프를 부르면 LangGraph가 부모 설정(체크포인터, 이름공간)을 물려받아
        하위 그래프로 취급한다. 그러면 Harness 작업 스레드가 근무조 스레드 밑에 저장되고, Harness의 승인
        interrupt가 근무조 그래프까지 올라온다. 부모 설정 컨텍스트를 비운 새 태스크에서 실행해 이를 막는다.
        """
        ctx = contextvars.copy_context()
        ctx.run(var_child_runnable_config.set, None)
        return await asyncio.create_task(coro, context=ctx)


__all__ = ["CommandError", "ShiftAgent", "ShiftState"]
