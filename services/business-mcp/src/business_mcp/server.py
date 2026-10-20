# Author: 정유은
# Date: 20OCT2026
# Purpose: business-mcp 도구 11개. 인자 검사, /internal/** 호출, 결과 이름 변환·길이 제한만 하고 업무 규칙은 갖지 않는다.

"""business-mcp 도구 (docs/MCP.md 3절). 도구와 내부 API 대응은 docs/specs/internal-api.yaml 대응표를 따른다.

- 위험 판정·변경 등급·승인 확인 같은 업무 규칙은 백엔드가 계산해서 돌려준 값을 그대로 전달한다.
- 결과 필드는 snake_case로 바꿔서 준다. Harness의 기사 범위 제한(`truck_id`)과 숫자 출처 검사가 이 이름을 쓴다.
- 승인 정보(approvalId, taskId)는 모델이 만들지 않도록 도구 인자가 아니라 HTTP 헤더로 받는다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Any, Literal, TypeVar

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers
from pydantic import Field

from .errors import InternalApiError, from_internal, tool_error
from .internal import InternalApi

Json = dict[str, Any]
T = TypeVar("T")
ApprovalProvider = Callable[[], tuple[str | None, str | None]]

INSTRUCTIONS = """\
따릉이 재배치 업무 도구. 모든 숫자(재고, 대수, 경로, 우선순위)는 백엔드·최적화기가 계산한 값이며 직접 만들지 않는다.
단위: 자전거 수는 대, 시간 길이는 _h(시간)·_minutes(분), 시각은 KST 오프셋을 포함한 ISO 8601.
변경 도구(add_constraint, submit_plan, propose_route_change)는 사람이 승인한 뒤에만 실행된다.
오류는 JSON(code, message, retryable, hint)으로 온다. retryable이 true면 hint의 값으로 인자를 고쳐 다시 호출한다.
"""

READ = {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False}
COMPUTE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
WRITE = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False}

APPROVAL_HEADER = "x-approval-id"
TASK_HEADER = "x-task-id"
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_CAMEL = re.compile(r"(?<=[a-z0-9])([A-Z])")

ConstraintType = Literal["TARGET_ADJUST", "STATION_SUSPENDED", "STATION_BLOCKED", "INVENTORY_OVERRIDE", "PRIORITY_BOOST"]
StationId = Annotated[str, Field(pattern=r"^ST-\d+$", description="대여소 ID, 예: ST-1023")]
ShiftId = Annotated[
    str,
    Field(pattern=r"^[a-z0-9-]+:\d{4}-\d{2}-\d{2}:(AM|PM|EVE)$",
          description="근무조 ID '{regionId}:{yyyy-MM-dd}:{AM|PM|EVE}', 예: gwangjin-seongdong:2026-10-21:AM"),
]


def to_snake(value: Any) -> Any:
    """백엔드 응답의 camelCase 키를 snake_case로 바꾼다. 값(enum 등)은 그대로 둔다."""
    if isinstance(value, dict):
        return {_CAMEL.sub(r"_\1", k).lower(): to_snake(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_snake(v) for v in value]
    return value


def headers_approval() -> tuple[str | None, str | None]:
    """Harness가 승인 뒤 붙이는 X-Approval-Id, X-Task-Id 헤더를 읽는다 (Streamable HTTP)."""
    headers = get_http_headers(include={APPROVAL_HEADER, TASK_HEADER})
    return headers.get(APPROVAL_HEADER), headers.get(TASK_HEADER)


def _check_dt(name: str, value: str) -> str:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        dt = None
    if dt is None or dt.tzinfo is None:
        raise tool_error(
            "INVALID_ARGUMENT",
            f"{name}는 KST 오프셋을 포함한 ISO 8601 시각이어야 합니다. 예: 2026-10-21T07:00:00+09:00",
            retryable=True, hint={"example": "2026-10-21T07:00:00+09:00"},
        )
    return dt.isoformat(timespec="seconds")


def _fit(payload: Json, list_key: str, max_chars: int) -> Json:
    """결과 JSON이 max_chars를 넘으면 목록 뒤쪽부터 잘라 내고 truncated·total을 단다 (Harness Tools)."""
    items = payload[list_key]
    payload.setdefault("truncated", False)
    payload.setdefault("total", len(items))
    while len(items) > 1 and len(json.dumps(payload, ensure_ascii=False)) > max_chars:
        items.pop()
        payload["truncated"] = True
    return payload


def _fit_plan(plan: Json, max_stops: int = 50, keep_per_truck: int = 10) -> Json:
    """정류장이 max_stops를 넘으면 트럭별 앞 keep_per_truck개만 남긴다."""
    total = sum(len(r.get("stops", [])) for r in plan.get("routes", []))
    if total > max_stops:
        for route in plan["routes"]:
            route["stops"] = route["stops"][:keep_per_truck]
        plan["truncated"] = True
        plan["total_stops"] = total
    return plan


def build_server(
    internal: InternalApi,
    *,
    approval: ApprovalProvider = headers_approval,
    max_chars: int = 4000,
) -> FastMCP:
    mcp = FastMCP("business-mcp", instructions=INSTRUCTIONS, version="0.1.0", mask_error_details=True)

    async def call(awaitable: Awaitable[T]) -> T:
        try:
            return to_snake(await awaitable)
        except InternalApiError as err:
            raise from_internal(err) from None

    def approved_change() -> Json:
        approval_id, task_id = approval()
        if not (approval_id and task_id and UUID_RE.match(approval_id) and UUID_RE.match(task_id)):
            raise tool_error(
                "APPROVAL_REQUIRED",
                "승인 정보가 없습니다. 사람이 승인하면 에이전트가 X-Approval-Id·X-Task-Id 헤더를 붙여 다시 호출합니다.",
                retryable=False,
            )
        return {"approvalId": approval_id, "taskId": task_id}

    # ── 조회 (readOnlyHint) ─────────────────────────────────

    @mcp.tool(annotations=READ, tags={"read"})
    async def get_risk_stations(
        region_id: Annotated[str, Field(description="권역 ID, 예: gwangjin-seongdong")],
        horizon_h: Annotated[int, Field(ge=1, le=5, description="몇 시간 앞까지 볼지(시간). 근거 문서상 3~5시간 예측이 검증 범위")] = 3,
        risk: Annotated[Literal["EMPTY", "OVERFLOW", "ALL"], Field(description="EMPTY = 빈 대여소 위험, OVERFLOW = 과적재")] = "ALL",
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
    ) -> Json:
        """권역에서 horizon_h시간 안에 빈 대여소 위험 또는 과적재가 예상되는 대여소를 우선순위 높은 순으로 돌려준다.

        반환: stations[] (station_id, name, bikes(대, 현재 재고), forecast_bikes(대, horizon_h 뒤 예상 재고),
        docks(거치대 수), risk, role, needed_bikes(대, 양수 회수·음수 배치), priority, empty_at(예상 빈 시각),
        reasons[], observed_at).
        """
        rows = await call(internal.risk_stations(region_id, horizon_h, limit))
        if risk != "ALL":
            rows = [r for r in rows if r.get("risk") == risk]
        return _fit({"source": "GET /internal/stations/risk", "horizon_h": horizon_h, "stations": rows},
                    "stations", max_chars)

    @mcp.tool(annotations=READ, tags={"read"})
    async def get_station_detail(station_id: StationId) -> Json:
        """대여소 한 곳의 현재 상태와 근거 데이터.

        반환: docks(거치대 수), bikes(대), observed_at(재고 관측 시각), forecast_bikes(대), risk,
        role(FIXED_PICKUP·FIXED_DROPOFF·VARIABLE), elevation_m(m), district, recent_reports[](actual_bikes(대)).
        """
        detail = await call(internal.station_detail(station_id))
        return {"source": f"GET /internal/stations/{station_id}", **detail}

    @mcp.tool(annotations=READ, tags={"read"})
    async def get_forecast(
        station_ids: Annotated[list[StationId], Field(min_length=1, max_length=20, description="최대 20곳")],
        start: Annotated[str, Field(description="예측 시작 시각(정시로 내림), 예: 2026-10-21T07:00:00+09:00")],
        horizon_h: Annotated[int, Field(ge=1, le=5, description="예측 구간(시간)")] = 3,
    ) -> Json:
        """대여소별 예측 구간 동안의 대여·반납 예측.

        반환: forecasts[] (station_id, start, horizon_h(시간), rentals(대, 구간 동안 나갈 자전거),
        returns(대, 구간 동안 들어올 자전거), model_version). 순유입은 returns - rentals.
        """
        rows = await call(internal.forecasts(station_ids, _check_dt("start", start), horizon_h))
        return _fit({"source": "GET /internal/forecasts", "forecasts": rows}, "forecasts", max_chars)

    @mcp.tool(annotations=READ, tags={"read"})
    async def get_current_plan(shift_id: ShiftId) -> Json:
        """근무조의 현재 계획.

        반환: plan_id, version, status(DRAFT·PROPOSED·ACTIVE·REJECTED·CLOSED), provisional(임시안 여부),
        routes[] (truck_id, capacity(대), start_load(대), stops[] (seq, station_id, action(PICKUP 회수·DROPOFF 배치),
        bikes(대), load_after(대), eta, reason, status(PENDING·DONE·SKIPPED))), unserved[] (station_id, priority, reason),
        explanation. 정류장이 50개를 넘으면 트럭별 앞 10개만 준다.
        """
        plan = await call(internal.current_plan(shift_id))
        return _fit_plan({"source": f"GET /internal/shifts/{shift_id}/plan", **plan})

    @mcp.tool(annotations=READ, tags={"read"})
    async def get_truck_status(
        shift_id: ShiftId,
        truck_id: Annotated[str | None, Field(description="트럭 ID, 예: TR-01. 비우면 전체")] = None,
    ) -> Json:
        """트럭별 현재 위치·적재량·남은 정류장.

        반환: trucks[] (truck_id, capacity(대), load(대, 현재 적재), lat, lon, shift_end(근무 종료 시각), remaining_stops[]).
        """
        trucks = await call(internal.truck_status(shift_id))
        if truck_id:
            ids = [t["truck_id"] for t in trucks]
            trucks = [t for t in trucks if t["truck_id"] == truck_id]
            if not trucks:
                raise tool_error("TRUCK_NOT_FOUND", f"'{truck_id}' 트럭이 근무조에 없습니다. 배정된 트럭: {', '.join(ids)}",
                                 retryable=True, hint={"candidates": ids})
        return {"source": f"GET /internal/shifts/{shift_id}/trucks", "trucks": trucks}

    @mcp.tool(annotations=READ, tags={"read"})
    async def get_field_reports(
        shift_id: ShiftId,
        since: Annotated[str | None, Field(description="이 시각 이후 보고만, ISO 8601")] = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
    ) -> Json:
        """기사 현장 보고 목록(최신순). 기사 이름은 마스킹돼 있다.

        반환: reports[] (report_id, truck_id, driver(마스킹), station_id, actual_bikes(대, 도착 때 실제 재고),
        issue(BLOCKED·CONSTRUCTION·INVENTORY_MISMATCH·OTHER), memo, reported_at).
        """
        rows = await call(internal.field_reports(shift_id, _check_dt("since", since) if since else None))
        rows = sorted(rows, key=lambda r: r["reported_at"], reverse=True)
        payload = {"source": f"GET /internal/shifts/{shift_id}/reports", "reports": rows[:limit],
                   "truncated": len(rows) > limit, "total": len(rows)}
        return _fit(payload, "reports", max_chars)

    @mcp.tool(annotations=READ, tags={"read"})
    async def get_audit(
        shift_id: ShiftId,
        since: Annotated[str | None, Field(description="이 시각 이후 기록만, ISO 8601")] = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
    ) -> Json:
        """근무조 감사 기록(최신순): 계획 활성화, 제약 추가, 경로 변경 적용, 현장 보고, 승인·거절.

        "오늘 이 구역 회수가 왜 많지?" 같은 질의의 근거로 쓴다 (PRD 6.7, FR-08).
        반환: events[] (audit_id, occurred_at, type, actor(role, display), target_id, detail).
        """
        rows = await call(internal.audit_events(shift_id, _check_dt("since", since) if since else None))
        rows = sorted(rows, key=lambda r: r["occurred_at"], reverse=True)
        payload = {"source": f"GET /internal/shifts/{shift_id}/audit", "events": rows[:limit],
                   "truncated": len(rows) > limit, "total": len(rows)}
        return _fit(payload, "events", max_chars)

    # ── 계산 ───────────────────────────────────────────────

    @mcp.tool(annotations=COMPUTE, tags={"compute"})
    async def request_route_plan(
        shift_id: ShiftId,
        kind: Annotated[Literal["PLAN", "REPLAN"], Field(description="PLAN = 근무조 계획 초안, REPLAN = 현재 위치·적재량에서 남은 정류장 재경로")] = "PLAN",
        truck_ids: Annotated[list[str] | None, Field(description="REPLAN에서 다시 풀 트럭. 비우면 전체")] = None,
        exclude_station_ids: Annotated[list[StationId] | None, Field(max_length=50, description="이번 계산에서 뺄 대여소 (진입 불가, 관리자 제외)")] = None,
    ) -> Json:
        """경로 계산을 요청하고 아직 활성화되지 않은 후보 계획을 돌려준다. 계산은 최적화기가 한다 (최대 약 60초).

        현장 실제 재고는 기사 보고로 백엔드에 이미 반영돼 있으므로 따로 넘기지 않는다.
        반환: candidate_id(경로 변경 적용 때 넘김), status(OK 또는 TIMEOUT_PROVISIONAL = 시간 초과로 만든 임시안),
        plan(get_current_plan과 같은 형식), grade(활성 계획 대비 변경 등급 NONE·MINOR·MAJOR, 백엔드 규칙),
        diffs[] (truck_id, before[], after[], added_minutes(분)).
        """
        request: Json = {"shiftId": shift_id, "kind": kind}
        if truck_ids:
            request["truckIds"] = truck_ids
        if exclude_station_ids:
            request["excludeStationIds"] = exclude_station_ids
        _, task_id = approval()
        if task_id and UUID_RE.match(task_id):
            request["requestedBy"] = {"taskId": task_id}
        candidate = await call(internal.solve(request))
        _fit_plan(candidate["plan"])
        result = {"source": "POST /internal/plans/solve", **candidate}
        if candidate.get("status") == "TIMEOUT_PROVISIONAL":
            result["notice"] = "최적화 시간이 초과돼 탐욕 휴리스틱으로 만든 임시안입니다. 화면에 임시안으로 표시하세요."
        return result

    # ── 변경 (destructiveHint, 승인 필요) ───────────────────

    @mcp.tool(annotations=WRITE, tags={"write"})
    async def add_constraint(
        shift_id: ShiftId,
        type: ConstraintType,
        station_ids: Annotated[list[StationId], Field(min_length=1, max_length=50)],
        valid_from: Annotated[str, Field(description="유효 시작 시각, ISO 8601")],
        valid_to: Annotated[str, Field(description="유효 종료 시각, ISO 8601")],
        reason: Annotated[str, Field(min_length=1, max_length=200, description="사람이 읽을 사유. 근거 패널·감사 로그에 표시")],
        value: Annotated[int | None, Field(description="TARGET_ADJUST는 가감 대수, INVENTORY_OVERRIDE는 실제 재고(대)")] = None,
    ) -> Json:
        """[승인 필요] 제약을 근무조에 저장한다. 다음 계산부터 반영된다.

        STATION_SUSPENDED(대여·반납 중지)는 그 기간 수요를 0으로 보고 배치 대상에서 뺀다.
        STATION_BLOCKED(트럭 진입 불가)는 경로에서 뺀다.
        반환: constraint_id와 저장된 제약.
        """
        start, end = _check_dt("valid_from", valid_from), _check_dt("valid_to", valid_to)
        if datetime.fromisoformat(start) >= datetime.fromisoformat(end):
            raise tool_error("INVALID_ARGUMENT", "valid_from은 valid_to보다 앞이어야 합니다.", retryable=True,
                             hint={"valid_from": start, "valid_to": end})
        if type in ("TARGET_ADJUST", "INVENTORY_OVERRIDE") and value is None:
            raise tool_error("INVALID_ARGUMENT", f"{type} 제약에는 value(대)가 필요합니다.", retryable=True,
                             hint={"required": "value"})
        request = {**approved_change(), "shiftId": shift_id, "type": type, "stationIds": station_ids,
                   "value": value, "validFrom": start, "validTo": end, "reason": reason}
        saved = await call(internal.add_constraint(request))
        return {"source": "POST /internal/constraints", **saved}

    @mcp.tool(annotations=WRITE, tags={"write"})
    async def submit_plan(
        plan_id: Annotated[str, Field(description="활성화할 계획 ID (request_route_plan 결과의 plan.plan_id)")],
    ) -> Json:
        """[승인 필요] 관리자가 승인한 계획을 활성화한다. 승인 기록과 감사 로그는 백엔드가 남긴다.

        반환: 활성화된 계획(status=ACTIVE). 더 새로운 계획이 있으면 PLAN_SUPERSEDED와 최신 계획 ID가 온다.
        """
        plan = await call(internal.activate_plan(plan_id, approved_change()))
        return _fit_plan({"source": f"POST /internal/plans/{plan_id}/activate", **plan})

    @mcp.tool(annotations=WRITE, tags={"write"})
    async def propose_route_change(
        truck_id: Annotated[str, Field(description="대상 트럭, 예: TR-01")],
        candidate_id: Annotated[str, Field(description="request_route_plan(kind=REPLAN) 결과의 candidate_id")],
    ) -> Json:
        """[승인 필요] 경로 변경 후보를 트럭에 적용한다. 승인 카드는 등급에 따라 기사(MINOR) 또는 관리자(MAJOR)에게 간다.

        승인 주체 확인은 백엔드가 한다.
        반환: 적용된 트럭 경로(truck_id, stops[]). 더 새로운 후보가 있으면 CANDIDATE_SUPERSEDED와 최신 후보 ID가 온다.
        """
        route = await call(internal.apply_route(truck_id, {**approved_change(), "candidateId": candidate_id}))
        return {"source": f"POST /internal/routes/{truck_id}/apply", **route}

    return mcp
