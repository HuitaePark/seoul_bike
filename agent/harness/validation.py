# Author: Cheon Seonghun
# Date: 20OCT2026
# Purpose: Validates model-written tool arguments against the internal API contract, known IDs, number sources,
#          ranges, and driver scope before any tool runs or any human is asked to approve.

"""Argument validation (docs/HARNESS.md section 3.6, PRD 6.8-3, NFR-06, NFR-07).

Tool arguments follow docs/specs internal-api (snake_case). Every call to a tool that computes or changes
something is checked before it runs and before an approval card is sent:

1. schema: names, types, enums, formats; unknown fields are rejected (the model cannot pass `approval_id`);
2. IDs: station IDs must have appeared in an earlier successful tool result or in the structured report;
   candidates and plans must come from an earlier `request_route_plan` result;
3. numbers: a constraint `value` must appear in the user's text, the structured report, or a tool result for
   that station (NFR-07), and a bike count may not exceed the station's docks;
4. scope: a driver may only recompute and change their own truck (NFR-06);
5. one change per turn: at most one approval-gated call per model response, so one RESUME answers one card.

A rejected response goes back to the model once with the reasons; a second rejection ends the task with
OUTPUT_INVALID.
"""
from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Iterable, Iterator
from datetime import datetime
from typing import Any, Literal

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import FailureCode, HarnessFailure
from .task import HarnessContext

TRUCK_ID = r"^TR-\d+$"
NUMBER = re.compile(r"\d+")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoutePlanArgs(_Strict):
    """internal-api SolveRequest."""

    shift_id: str
    kind: Literal["PLAN", "REPLAN"]
    truck_ids: list[str] = Field(default_factory=list)
    exclude_station_ids: list[str] = Field(default_factory=list)


class AddConstraintArgs(_Strict):
    """internal-api ConstraintRequest (approval_id, task_id are filled by Harness)."""

    shift_id: str
    type: Literal["TARGET_ADJUST", "STATION_SUSPENDED", "STATION_BLOCKED", "INVENTORY_OVERRIDE", "PRIORITY_BOOST"]
    station_ids: list[str] = Field(min_length=1)
    valid_from: datetime
    valid_to: datetime
    reason: str = Field(min_length=1)
    value: int | None = None


class SubmitPlanArgs(_Strict):
    plan_id: str = Field(min_length=1)


class ProposeRouteArgs(_Strict):
    """internal-api applyRouteChange: apply one truck's route from a computed candidate."""

    truck_id: str = Field(pattern=TRUCK_ID)
    candidate_id: str = Field(min_length=1)


SCHEMAS: dict[str, type[_Strict]] = {
    "business_request_route_plan": RoutePlanArgs,
    "business_add_constraint": AddConstraintArgs,
    "business_submit_plan": SubmitPlanArgs,
    "business_propose_route_change": ProposeRouteArgs,
}


def _objects(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from _objects(v)
    elif isinstance(value, list):
        for v in value:
            yield from _objects(v)


class _Facts:
    """What the model may legitimately refer to, collected from successful tool results and the task."""

    def __init__(self, history: list[BaseMessage], id_pattern: re.Pattern[str], ctx: HarnessContext) -> None:
        self.ids: set[str] = set()
        self.docks: dict[str, int] = {}
        self.counts: dict[str, set[int]] = {}  # station -> bike counts stated by tools or the report
        self.candidates: dict[str, dict[str, Any]] = {}
        for m in history:
            if not isinstance(m, ToolMessage) or m.status == "error":
                continue
            text = str(m.content)
            self.ids.update(id_pattern.findall(text))
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            for obj in _objects(parsed):
                if "candidateId" in obj:
                    self.candidates[obj["candidateId"]] = obj
                station = obj.get("stationId")
                if not isinstance(station, str):
                    continue
                if isinstance(obj.get("docks"), int):
                    self.docks[station] = obj["docks"]
                for key in ("bikes", "actualBikes"):
                    if isinstance(obj.get(key), int):
                        self.counts.setdefault(station, set()).add(obj[key])
        report = ctx.task.report
        if report:
            self.ids.add(report.station_id)
            self.counts.setdefault(report.station_id, set()).add(report.actual_bikes)
        self.input_numbers = {int(n) for n in NUMBER.findall(ctx.task.input)}
        self.plan_ids = {c.get("plan", {}).get("planId") for c in self.candidates.values()}


def _last_ai(response: ModelResponse | AIMessage) -> AIMessage | None:
    if isinstance(response, AIMessage):
        return response
    return next((m for m in reversed(response.result) if isinstance(m, AIMessage)), None)


class ArgumentValidationMiddleware(AgentMiddleware):
    """Runs inside LoopGuard and before the approval interrupt (see module docstring)."""

    def __init__(self, validated_tools: set[str], id_pattern: str, gated: Iterable[str]) -> None:
        super().__init__()
        self.validated = frozenset(validated_tools) & SCHEMAS.keys()
        self.gated = frozenset(gated)
        self.id_pattern = re.compile(id_pattern)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | AIMessage:
        ctx: HarnessContext = request.runtime.context
        response = await handler(request)
        ai = _last_ai(response)
        problems = self.problems(ai, request.messages, ctx)
        if not problems:
            return response
        feedback = [
            ToolMessage(
                content=problems.get(tc["id"], "실행되지 않음: 같은 응답의 다른 도구 호출이 검증에 실패했다."),
                tool_call_id=tc["id"],
                name=tc["name"],
                status="error",
            )
            for tc in ai.tool_calls
        ]
        retry = await handler(request.override(messages=[*request.messages, ai, *feedback]))
        if still := self.problems(_last_ai(retry), request.messages, ctx):
            raise HarnessFailure(FailureCode.OUTPUT_INVALID, "; ".join(still.values()))
        return retry

    def problems(self, ai: AIMessage | None, history: list[BaseMessage], ctx: HarnessContext) -> dict[str, str]:
        if ai is None or not ai.tool_calls:
            return {}
        found: dict[str, str] = {}
        gated_calls = [tc for tc in ai.tool_calls if tc["name"] in self.gated]
        for tc in gated_calls[1:]:
            found[tc["id"]] = f"{tc['name']} 거부: 변경 도구는 한 번에 하나만 부른다. 앞의 변경이 승인된 뒤 다시 부른다"
        calls = [tc for tc in ai.tool_calls if tc["name"] in self.validated and tc["id"] not in found]
        if not calls:
            return found
        facts = _Facts(history, self.id_pattern, ctx)
        for tc in calls:
            if reasons := self._check(tc["name"], tc["args"], facts, ctx):
                found[tc["id"]] = f"{tc['name']} 거부: " + " / ".join(reasons)
        return found

    def _check(self, name: str, args: dict[str, Any], facts: _Facts, ctx: HarnessContext) -> list[str]:
        try:
            parsed = SCHEMAS[name].model_validate(args)
        except ValidationError as exc:
            return [f"인자 형식 오류 {e['loc']}: {e['msg']}" for e in exc.errors(include_url=False)]
        reasons = []
        used = set(self.id_pattern.findall(json.dumps(args, ensure_ascii=False)))
        if unknown := sorted(used - facts.ids):
            reasons.append(f"조회 결과에 없는 대여소 ID {', '.join(unknown)}. 조회 도구로 먼저 확인한 ID만 쓴다")
        requester = ctx.task.requester
        own = requester.truck_id if requester.role == "driver" else None
        if isinstance(parsed, RoutePlanArgs) and own:
            if parsed.kind != "REPLAN" or parsed.truck_ids != [own]:
                reasons.append(f"기사는 자기 트럭만 다시 계산한다: kind=REPLAN, truck_ids=[{own}]")
        if isinstance(parsed, AddConstraintArgs):
            reasons += self._constraint(parsed, facts)
        if isinstance(parsed, SubmitPlanArgs) and parsed.plan_id not in facts.plan_ids:
            reasons.append(f"계산 결과에 없는 계획 {parsed.plan_id}. request_route_plan 결과의 planId만 제출한다")
        if isinstance(parsed, ProposeRouteArgs):
            reasons += self._route_change(parsed, facts, own)
        return reasons

    @staticmethod
    def _constraint(args: AddConstraintArgs, facts: _Facts) -> list[str]:
        reasons = []
        if args.valid_to <= args.valid_from:
            reasons.append("valid_to는 valid_from보다 뒤여야 한다")
        needs_value = args.type in {"TARGET_ADJUST", "INVENTORY_OVERRIDE"}
        if needs_value and args.value is None:
            reasons.append(f"{args.type}에는 value가 필요하다")
        if not needs_value and args.value is not None:
            reasons.append(f"{args.type}에는 value를 쓰지 않는다")
        if args.value is None or not needs_value:
            return reasons
        if args.type == "INVENTORY_OVERRIDE":
            if len(args.station_ids) != 1:
                reasons.append("INVENTORY_OVERRIDE는 대여소 하나에만 쓴다")
            station = args.station_ids[0]
            stated = facts.counts.get(station, set()) | facts.input_numbers
            if args.value not in stated:
                reasons.append(
                    f"재고 {args.value}대의 출처가 없다. 보고·조회 결과·요청 문장에 있는 값만 쓴다 "
                    f"({station}: {sorted(facts.counts.get(station, set())) or '없음'})"
                )
        elif abs(args.value) not in facts.input_numbers:
            reasons.append(f"조정 대수 {args.value}대의 출처가 없다. 요청 문장에 있는 값만 쓴다")
        for station in args.station_ids:
            if (docks := facts.docks.get(station)) is not None and abs(args.value) > docks:
                reasons.append(f"{station} 거치대 수 {docks}대를 넘는 값 {args.value}대")
        return reasons

    @staticmethod
    def _route_change(args: ProposeRouteArgs, facts: _Facts, own: str | None) -> list[str]:
        candidate = facts.candidates.get(args.candidate_id)
        if candidate is None:
            return [f"계산 결과에 없는 후보 {args.candidate_id}. request_route_plan 결과의 candidateId만 쓴다"]
        reasons = []
        trucks = {r.get("truckId") for r in candidate.get("plan", {}).get("routes", [])}
        if args.truck_id not in trucks:
            reasons.append(f"후보 {args.candidate_id}에 {args.truck_id} 경로가 없다")
        if candidate.get("grade") == "NONE" or not candidate.get("requiredApprovers"):
            reasons.append("변경 등급 NONE(승인 순서 없음): 바뀐 것이 없어 적용하지 않는다")
        if own and args.truck_id != own:
            reasons.append(f"기사는 자기 트럭({own})의 경로만 바꿀 수 있다")
        return reasons
