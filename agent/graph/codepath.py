# Author: Jung Minkyu
# Date: 20OCT2026
# Purpose: LLM 없이 도는 대체 경로 (NFR-03). 도구를 코드로 불러 계획 초안·메모 없는 현장 보고를 처리하고 템플릿으로 설명한다.

"""LLM 없는 경로 (docs/AGENT-GRAPH.md 4절).

언제 쓰나
- 모델을 끈 운영(`AGENT_LLM=none`), 또는 Harness가 MODEL_UNAVAILABLE·MODEL_ERROR로 끝난 계획 초안·현장 보고
- 메모 없는 현장 보고: 숫자뿐이라 모델이 해석할 것이 없고, 재경로 10초(NFR-01)에 들어가기 쉽다

지키는 것
- 숫자는 도구 결과와 기사 보고 값만 옮긴다 (NFR-07)
- 카드·승인 순서·결정 변환은 Harness와 같은 함수(`harness.contract`)를 쓴다. 그래서 모델 경로와 카드 모양,
  `approvalId`, 큰 변경의 관리자 → 기사 두 장이 같다
- 변경 도구는 승인 순서의 마지막 카드까지 승인된 뒤에만 부르고, 그 카드의 `approval_id`와 `task_id`를 넣는다
  (internal-api `ApprovedChange`). 승인 대기는 Harness interrupt 대신 근무조 상태의 작업 기록(`code`)에 남긴다.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

from harness import HarnessConfig, Task
from harness.contract import approval_card, approval_id, approvers, decision_from_resume, sources

from .approvals import doc_sources, enrich_card, merge_sources, tool_results
from .contract import base_name, report_contract
from .events import ShiftEventSink


def failed(code: str, message: str) -> dict:
    return {"status": "FAILED", "code": code, "message": message}


class ToolMissing(LookupError):
    """대체 경로에 필요한 도구가 MCP 서버에 없다."""


class ToolFailed(RuntimeError):
    """도구가 오류로 끝났다 (MCP `isError`, 예: backend 403·409). MCP 어댑터는 예외 대신 오류 글을 돌려주므로 상태로 가린다."""


def _text(raw: Any) -> str:
    """MCP 도구 결과(문자열 또는 내용 블록 목록) → 문자열."""
    if isinstance(raw, list):
        return "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in raw)
    return raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


class CodePath:
    """도구를 코드로 부르는 대체 경로. 도구 인자는 도구 스키마에 있는 이름만 골라 넘긴다."""

    def __init__(self, tools: Iterable[BaseTool], config: HarnessConfig) -> None:
        self.tools = {t.name: t for t in tools}
        self.rules = config.guardrails.approval
        self.labels = config.observability.tool_labels

    def tool(self, name: str) -> BaseTool:
        found = self.tools.get(f"business_{name}") or self.tools.get(name)
        if found is None:
            raise ToolMissing(name)
        return found

    def args_for(self, name: str, **candidates: Any) -> dict:
        """후보 인자 중 도구 스키마에 있는 것만 남긴다. 값이 None이면 뺀다."""
        props = set((self.tool(name).args or {}).keys())
        return {k: v for k, v in candidates.items() if k in props and v is not None}

    async def call(self, name: str, record: dict | None = None, sink: ShiftEventSink | None = None,
                   trail: list | None = None, **candidates: Any) -> Any:
        """도구 1회 호출. sink가 있으면 TOOL_START·TOOL_END를 내고, trail에 결과(ToolMessage)를 남긴다 (카드·근거용)."""
        tool = self.tool(name)
        args = self.args_for(name, **candidates)
        label = self.labels.get(tool.name, tool.name)
        if sink:
            sink.send(record, "TOOL_START", tool={"name": tool.name, "label": label})
        ok = False
        try:
            # tool call로 부르면 오류가 ToolMessage.status="error"로 온다. 인자만 넘기면 오류 글이 결과처럼 돌아온다
            message = await tool.ainvoke({"type": "tool_call", "name": tool.name, "args": args,
                                          "id": f"code-{uuid.uuid4().hex[:12]}"})
            text = _text(message.content)
            ok = getattr(message, "status", "success") != "error"
        finally:
            if sink:
                sink.send(record, "TOOL_END", tool={"name": tool.name, "label": label, "ok": ok})
        if not ok:
            raise ToolFailed(f"{tool.name}: {text}")
        if trail is not None:
            trail.append(ToolMessage(content=text, name=tool.name, tool_call_id=f"code-{len(trail) + 1}"))
        return _json(text)

    # ---- 그래프가 따로 쓰는 읽기 도구 (이벤트 없이) ----

    async def find_report(self, shift_id: str, report_id: str) -> dict | None:
        """명령에 보고 값이 없을 때(G2) 근무조 보고 목록에서 찾는다."""
        result = await self.call("get_field_reports", shift_id=shift_id)
        # MCP 명세 2.2: 목록 결과는 {source, reports: [...], truncated, total}로 감싼다. 평가용 가짜 MCP는 목록 그대로 준다
        rows = result.get("reports", []) if isinstance(result, dict) else result
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and row.get("reportId") == report_id:
                return report_contract(row)
        return None

    # ---- 작업 처리 ----

    async def run(self, record: dict, task: Task, sink: ShiftEventSink, resume: dict | None = None) -> dict:
        """작업 1구간을 처리하고 결과(outcome)를 돌려준다. resume이 있으면 그 결정에 따른 재개다.

        결정이 대기 카드와 맞지 않으면 `harness.contract.ApprovalMismatch` (아무것도 실행하지 않음).
        """
        if record["type"] not in {"PLAN_DRAFT", "FIELD_REPORT"}:
            return failed("MODEL_UNAVAILABLE", "모델 없이 처리할 수 없는 작업이다 (자연어 해석이 필요하다)")
        if resume is None:
            return await self._start(record, task, sink, exclude=record.get("excluded") or [])
        return await self._resume(record, task, sink, resume)

    async def _start(self, record: dict, task: Task, sink: ShiftEventSink, exclude: list[str]) -> dict:
        if record["type"] == "PLAN_DRAFT":
            return await self._plan_draft(record, task, sink, exclude)
        return await self._field_report(record, task, sink, exclude)

    async def _plan_draft(self, record: dict, task: Task, sink: ShiftEventSink, exclude: list[str]) -> dict:
        trail: list = []
        candidate = await self.call("request_route_plan", record, sink, trail, shift_id=record["shiftId"], kind="PLAN",
                                    truck_ids=[], exclude_station_ids=list(dict.fromkeys(exclude)))
        if not isinstance(candidate, dict) or not candidate.get("plan", {}).get("planId"):
            return failed("TOOL_UNAVAILABLE", "경로 계산 결과를 읽지 못했다")
        call = {"name": self.tool("submit_plan").name, "args": {"plan_id": candidate["plan"]["planId"]},
                "id": f"code-{uuid.uuid4().hex[:12]}"}
        return self._ask(record, task, sink, call, trail, 0, exclude)

    async def _field_report(self, record: dict, task: Task, sink: ShiftEventSink, exclude: list[str]) -> dict:
        report = record.get("report") or {}
        truck = report.get("truckId")
        if not truck or not report.get("stationId"):
            return failed("OUTPUT_INVALID", "현장 보고에 트럭·대여소가 없다")
        trail: list = []
        blocked = [report["stationId"]] if report.get("issue") in {"BLOCKED", "CONSTRUCTION"} else []
        candidate = await self.call("request_route_plan", record, sink, trail, shift_id=record["shiftId"], kind="REPLAN",
                                    truck_ids=[truck], exclude_station_ids=list(dict.fromkeys([*blocked, *exclude])))
        if not isinstance(candidate, dict) or "candidateId" not in candidate:
            return failed("TOOL_UNAVAILABLE", "경로 계산 결과를 읽지 못했다")
        diff = next((d for d in candidate.get("diffs") or [] if d.get("truckId") == truck), None)
        if diff is None or not candidate.get("requiredApprovers"):  # 등급 NONE: 바꿀 것이 없다 (internal-api v0.2)
            answer = f"{truck} 보고를 반영해 다시 계산했지만 경로는 그대로입니다."
            used = merge_sources(sources(trail, self.labels), doc_sources(trail))
            sink.send(record, "DONE", answer=answer, sources=used)
            return {"status": "DONE", "answer": answer, "sources": used}
        call = {"name": self.tool("propose_route_change").name,
                "args": {"truck_id": truck, "candidate_id": candidate["candidateId"]},
                "id": f"code-{uuid.uuid4().hex[:12]}"}
        return self._ask(record, task, sink, call, trail, 0, exclude)

    def _ask(self, record: dict, task: Task, sink: ShiftEventSink, call: dict, trail: list, step: int,
             exclude: Iterable[str] = ()) -> dict:
        """승인 순서의 `step`번째 카드를 낸다. 대기 상태(멈춘 호출, 근거가 된 도구 결과, 단계)를 결과에 담아 저장한다."""
        card = enrich_card(approval_card(task, call, trail, self.rules, step), tool_results(trail), exclude)
        sink.send(record, "APPROVAL_REQUIRED", approval=card)
        return {
            "status": "APPROVAL_REQUIRED",
            "approval": card,
            "pending": {"call": call, "results": [{"name": m.name, "content": m.content} for m in trail], "step": step},
            "excluded": list(exclude),
        }

    async def _resume(self, record: dict, task: Task, sink: ShiftEventSink, resume: dict) -> dict:
        pending = record["pending"]
        call, step = pending["call"], pending["step"]
        trail = [ToolMessage(content=r["content"], name=r["name"], tool_call_id=f"code-{i + 1}")
                 for i, r in enumerate(pending["results"])]
        card = approval_card(task, call, trail, self.rules, step)
        decision = decision_from_resume(resume, card)  # 다른 카드·다른 승인자면 ApprovalMismatch
        order = approvers(call, trail, self.rules)
        if decision["type"] == "approve" and step + 1 < len(order):
            return self._ask(record, task, sink, call, trail, step + 1, record.get("excluded") or [])
        if resume.get("excludeStationIds"):  # 일부 제외: 그대로 실행하지 않고 빼고 다시 계산한다 (UR-04)
            return await self._start(record, task, sink, [*(record.get("excluded") or []), *resume["excludeStationIds"]])
        summary = card["summary"]
        if decision["type"] == "reject":
            answer = f"거절되어 실행하지 않았습니다: {summary}"
            if resume.get("comment"):
                answer += f" (사유: {resume['comment']})"
            sink.send(record, "DONE", answer=answer, sources=[])
            return {"status": "DONE", "answer": answer, "rejected": True}
        final = approval_id(task.task_id, call["id"], order[-1])  # 마지막 카드(기사 수락)의 ID로 변경 API를 부른다
        await self.call(base_name(call["name"]), record, sink, trail, **call["args"], approval_id=final,
                        task_id=task.task_id)
        answer = "\n".join([f"승인되어 실행했습니다: {summary}", *card.get("reasons", [])])
        used = merge_sources(sources(trail, self.labels), doc_sources(trail))
        sink.send(record, "DONE", answer=answer, sources=used)
        return {"status": "DONE", "answer": answer, "executed": True, "sources": used}
