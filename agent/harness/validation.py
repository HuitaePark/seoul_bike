# Author: Cheon Seonghun
# Date: 20OCT2026
# Purpose: Validates model-written tool arguments against Pydantic schemas, known IDs, number sources, and ranges.

"""Argument validation (docs/HARNESS.md section 3.6, PRD 6.8-3, NFR-06, NFR-07).

Every call to a tool that carries constraints or changes something is checked before it runs and before any
human is asked to approve it:

1. schema: argument names, types, enums, and formats (unknown fields are rejected);
2. IDs: station IDs must have appeared in an earlier successful tool result or in the structured report;
3. numbers: a bike count in a constraint must equal the count in the structured driver report (NFR-07);
4. range: a bike count may not exceed the station's dock capacity when a tool result stated it;
5. scope: a driver may only propose changes for their own truck (NFR-06).

A rejected call goes back to the model once with the reasons; a second rejection ends the task with
OUTPUT_INVALID.
"""
from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Iterator
from typing import Annotated, Any, Literal

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import FailureCode, HarnessFailure
from .task import HarnessContext

PERIOD = r"^(\d{4}-\d{2}-\d{2} )?\d{2}:\d{2}~\d{2}:\d{2}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Blocked(_Strict):
    type: Literal["blocked"]
    station_id: str


class Suspended(_Strict):
    type: Literal["suspended"]
    station_ids: list[str] = Field(min_length=1)


class Excluded(_Strict):
    """Stations a manager removed from the plan (FR-04 '거점 제외 후 승인')."""

    type: Literal["excluded"]
    station_ids: list[str] = Field(min_length=1)


class ActualBikes(_Strict):
    type: Literal["actual_bikes"]
    station_id: str
    bikes: int = Field(ge=0)


Constraint = Annotated[Blocked | Suspended | Excluded | ActualBikes, Field(discriminator="type")]


class RoutePlanArgs(_Strict):
    shift_id: str
    constraints: list[Constraint] = Field(default_factory=list)


class AddConstraintArgs(_Strict):
    type: Literal["suspended", "blocked"]
    station_ids: list[str] = Field(min_length=1)
    period: str = Field(pattern=PERIOD)
    reason: str = Field(min_length=1)


class SubmitPlanArgs(_Strict):
    plan_id: str = Field(min_length=1)


class ProposeRouteArgs(_Strict):
    truck_id: str = Field(pattern=r"^TR-\d+$")
    route: list[str]


SCHEMAS: dict[str, type[_Strict]] = {
    "business_request_route_plan": RoutePlanArgs,
    "business_add_constraint": AddConstraintArgs,
    "business_submit_plan": SubmitPlanArgs,
    "business_propose_route_change": ProposeRouteArgs,
}


def _json_objects(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from _json_objects(v)
    elif isinstance(value, list):
        for v in value:
            yield from _json_objects(v)


class _Facts:
    """What the model may legitimately refer to: IDs and capacities from successful tool results."""

    def __init__(self, history: list[BaseMessage], id_pattern: re.Pattern[str], ctx: HarnessContext) -> None:
        self.ids: set[str] = set()
        self.capacity: dict[str, int] = {}
        for m in history:
            if not isinstance(m, ToolMessage) or m.status == "error":
                continue
            text = str(m.content)
            self.ids.update(id_pattern.findall(text))
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            for obj in _json_objects(parsed):
                if isinstance(obj.get("station_id"), str) and isinstance(obj.get("capacity"), int):
                    self.capacity[obj["station_id"]] = obj["capacity"]
        if ctx.task.report:
            self.ids.add(ctx.task.report.station_id)


def _last_ai(response: ModelResponse | AIMessage) -> AIMessage | None:
    if isinstance(response, AIMessage):
        return response
    return next((m for m in reversed(response.result) if isinstance(m, AIMessage)), None)


class ArgumentValidationMiddleware(AgentMiddleware):
    """Runs inside LoopGuard and before the approval interrupt (see module docstring)."""

    def __init__(self, validated_tools: set[str], id_pattern: str) -> None:
        super().__init__()
        self.validated = frozenset(validated_tools) & SCHEMAS.keys()
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
        if ai is None:
            return {}
        calls = [tc for tc in ai.tool_calls if tc["name"] in self.validated]
        if not calls:
            return {}
        facts = _Facts(history, self.id_pattern, ctx)
        found = {}
        for tc in calls:
            reasons = self._check(tc["name"], tc["args"], facts, ctx)
            if reasons:
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
        if isinstance(parsed, RoutePlanArgs):
            reasons += self._bike_counts(parsed, facts, ctx)
        if isinstance(parsed, ProposeRouteArgs) and ctx.task.requester.role == "driver":
            if parsed.truck_id != ctx.task.requester.truck_id:
                reasons.append(f"기사는 자기 트럭({ctx.task.requester.truck_id})의 경로만 제안할 수 있다")
        return reasons

    @staticmethod
    def _bike_counts(args: RoutePlanArgs, facts: _Facts, ctx: HarnessContext) -> list[str]:
        reasons = []
        report = ctx.task.report
        for c in args.constraints:
            if not isinstance(c, ActualBikes):
                continue
            if report is None or report.actual_bikes is None:
                reasons.append("실제 재고 숫자는 기사 보고 데이터에서만 온다. 이 작업에는 보고된 재고가 없다")
            elif (c.station_id, c.bikes) != (report.station_id, report.actual_bikes):
                reasons.append(
                    f"보고 데이터와 다르다: 보고는 {report.station_id} {report.actual_bikes}대, "
                    f"제약은 {c.station_id} {c.bikes}대. 보고 값을 그대로 쓴다"
                )
            if (cap := facts.capacity.get(c.station_id)) is not None and c.bikes > cap:
                reasons.append(f"{c.station_id} 거치대 수 {cap}대를 넘는 재고 {c.bikes}대")
        return reasons
