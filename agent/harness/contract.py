# Author: Cheon Seonghun
# Date: 20OCT2026
# Purpose: Maps the team contract (docs/specs) to Harness types: ai.tasks -> Task, RESUME -> decision, approval card.

"""Contract mapping (docs/specs asyncapi `TaskCommand`, openapi `ApprovalRequest`, `TaskEvent`).

Approval follows docs/specs: a plan or constraint gets one MANAGER card. A route change gets one card per entry
of the candidate's `requiredApprovers`, in order (minor `[DRIVER]`, major `[MANAGER, DRIVER]`, decided by the
backend); the driver's card is the driver's acceptance, so there is no separate acceptance step. A rejection at
any card stops the sequence and keeps the current route.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from langchain_core.messages import BaseMessage, ToolMessage

from .config import ApprovalRule
from .task import FieldReport, Requester, Task

TASK_TYPES = {"PLAN_DRAFT": "plan_draft", "FIELD_REPORT": "field_report", "REQUEST": "manager_request", "QUESTION": "qa"}
ROLES = {"MANAGER": "manager", "DRIVER": "driver", "EXECUTIVE": "executive"}
APPROVAL_NAMESPACE = uuid.UUID("5b1d6c55-3a8e-4c84-9f5a-6d6f1f0a2e11")


class ApprovalMismatch(ValueError):
    """A RESUME answers a different approval or comes from the wrong approver. Nothing runs."""


def task_from_command(
    command: Mapping[str, Any],
    *,
    report: FieldReport | None = None,
    brief: str = "",
    driver_names: Iterable[str] = (),
) -> Task:
    """New-task `ai.tasks` message -> Task. For FIELD_REPORT the caller loads the report by `reportId`; a driver's
    truck comes from that report because the contract's `requestedBy` has no truck id (docs/ISSUES.md ISS-04)."""
    if command["type"] not in TASK_TYPES:
        raise ValueError(f"not a new task: {command['type']}")
    requested = command["requestedBy"]
    role = ROLES[requested["role"]]
    truck = requested.get("truckId") or (report.truck_id if report and role == "driver" else None)
    text = command.get("text") or (report.memo if report else None) or ""
    return Task(
        task_id=command["taskId"],
        trace_id=command.get("traceId") or command["eventId"],
        task_type=TASK_TYPES[command["type"]],  # type: ignore[arg-type]
        shift_id=command["shiftId"],
        input=text,
        brief=brief,
        driver_names=tuple(driver_names),
        requester=Requester(role, truck),  # type: ignore[arg-type]
        report=report,
    )


def approval_id(task_id: str, tool_call_id: str, approver: str) -> str:
    """Stable id for one approval card: one gated tool call and one approver. The same card always gets the same
    id, so it survives the interrupt node re-running and agent restarts, and the backend can match RESUME to it."""
    return str(uuid.uuid5(APPROVAL_NAMESPACE, f"{task_id}:{tool_call_id}:{approver}"))


def _tool_results(history: Iterable[BaseMessage]) -> list[Any]:
    out = []
    for m in history:
        if isinstance(m, ToolMessage) and m.status != "error":
            try:
                out.append(json.loads(m.content))
            except (json.JSONDecodeError, TypeError):
                continue
    return out


def candidates(history: Iterable[BaseMessage]) -> dict[str, dict[str, Any]]:
    """Route plan candidates (internal-api `PlanCandidate`) seen in the task, by `candidateId`."""
    return {r["candidateId"]: r for r in _tool_results(history) if isinstance(r, dict) and "candidateId" in r}


def approvers(call: Mapping[str, Any], history: list[BaseMessage], rules: Mapping[str, ApprovalRule]) -> list[str]:
    """Approval order for a gated call: fixed for plans and constraints, the candidate's `requiredApprovers` for
    route changes (MINOR `[DRIVER]`, MAJOR `[MANAGER, DRIVER]`, decided by the backend)."""
    rule = rules[call["name"]]
    if rule.approver:
        return [rule.approver]
    return list(candidates(history).get(call["args"].get("candidate_id"), {}).get("requiredApprovers") or [])


def final_approval_id(task: Task, call: Mapping[str, Any], history: list[BaseMessage], rules: Mapping[str, ApprovalRule]) -> str:
    """The id sent with the change itself: the last card of the order (for a route change, the driver's acceptance).
    The backend checks that every card of the order before it was approved too."""
    order = approvers(call, history, rules)
    return approval_id(task.task_id, call["id"], order[-1])


def approval_card(
    task: Task, call: Mapping[str, Any], history: list[BaseMessage], rules: Mapping[str, ApprovalRule], step: int = 0
) -> dict[str, Any]:
    """Contract `ApprovalRequest` for card number `step` of a gated tool call. Numbers come from tool results."""
    rule = rules[call["name"]]
    args = call["args"]
    order = approvers(call, history, rules)
    approver = order[step]
    card: dict[str, Any] = {
        "approvalId": approval_id(task.task_id, call["id"], approver),
        "kind": rule.kind,
        "approver": approver,
        "grade": None,
        "candidateId": None,
        "truckId": args.get("truck_id"),
        "planId": None,
        "reasons": [],
        "diff": None,
    }
    if rule.kind == "ROUTE_CHANGE":
        candidate = candidates(history).get(args.get("candidate_id"), {})
        grade = candidate.get("grade")
        card |= {"grade": grade, "candidateId": args.get("candidate_id"), "requiredApprovers": order}
        card["planId"] = candidate.get("plan", {}).get("planId")
        card["diff"] = next((d for d in candidate.get("diffs", []) if d.get("truckId") == args.get("truck_id")), None)
        size = {"MINOR": "작은 변경", "MAJOR": "큰 변경"}.get(grade, "변경")
        role = {"MANAGER": "관리자 승인", "DRIVER": "기사 수락"}[approver]
        card["summary"] = f"{args.get('truck_id')} 경로 변경 ({size}, {role} {step + 1}/{len(order)})"
        if card["diff"] and (card["diff"].get("addedMinutes") or 0) > 0:
            card["reasons"].append(f"소요 시간 {card['diff']['addedMinutes']}분 증가")
    elif rule.kind == "SUBMIT_PLAN":
        card["planId"] = args.get("plan_id")
        plan = next(
            (c["plan"] for c in candidates(history).values() if c.get("plan", {}).get("planId") == args.get("plan_id")),
            {},
        )
        card["summary"] = f"계획 {args.get('plan_id')} 활성화"
        if plan:
            card["reasons"].append(f"포기 대여소 {len(plan.get('unserved', []))}곳")
    else:
        stations = ", ".join(args.get("station_ids", []))
        card["summary"] = f"제약 추가 {args.get('type')}: {stations}"
        if args.get("reason"):
            card["reasons"].append(args["reason"])
    return card


def decision_from_resume(resume: Mapping[str, Any], card: Mapping[str, Any]) -> dict[str, Any]:
    """Contract RESUME -> Harness decision.

    - wrong `approvalId` or `decidedBy.role` -> ApprovalMismatch (the backend should already have answered 403);
    - `excludeStationIds` -> reject with an instruction to recompute without those stations (FR-04), because the
      reviewed plan still contains them and must not be activated;
    - REJECT -> reject with the comment; APPROVE -> approve.
    """
    if resume["approvalId"] != card["approvalId"]:
        raise ApprovalMismatch(f"pending approval is {card['approvalId']}, RESUME answers {resume['approvalId']}")
    role = resume.get("decidedBy", {}).get("role")
    if role != card["approver"]:
        raise ApprovalMismatch(f"{card['kind']} needs {card['approver']}, decided by {role}")
    comment = (resume.get("comment") or "").strip()
    if excluded := resume.get("excludeStationIds"):
        return {
            "type": "reject",
            "message": (
                f"관리자가 대여소 {', '.join(excluded)}를 빼라고 했다. exclude_station_ids에 넣어 다시 계산하고 "
                f"새 계획으로 다시 승인을 요청한다. {comment}"
            ).strip(),
        }
    if resume["decision"] == "REJECT":
        return {"type": "reject", "message": comment or "거절됨. 기존 계획과 경로를 유지한다."}
    return {"type": "approve"}


def sources(history: Iterable[BaseMessage], labels: Mapping[str, str]) -> list[dict[str, Any]]:
    """Contract `Source` list for DONE: documents found by RAG and the tools whose results were used."""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in history:
        if not isinstance(m, ToolMessage) or m.status == "error" or not m.name:
            continue
        if m.name.startswith("rag_"):
            try:
                hits = json.loads(m.content)
            except json.JSONDecodeError:
                continue
            for hit in hits if isinstance(hits, list) else []:
                uri = hit.get("doc_id")
                if uri and uri not in seen:
                    seen.add(uri)
                    found.append({"kind": "DOC", "title": hit.get("source", uri), "uri": uri, "excerpt": hit.get("text", "")[:120]})
        elif m.name not in seen:
            seen.add(m.name)
            found.append({"kind": "TOOL", "title": labels.get(m.name, m.name).removesuffix(" 중"), "uri": None, "excerpt": None})
    return found
