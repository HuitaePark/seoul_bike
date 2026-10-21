# Author: 정유은
# Date: 20OCT2026
# Purpose: business-mcp 도구 11개. 인자 검사, /internal/** 호출, 결과 길이 제한만 하고 업무 규칙은 갖지 않는다.

"""business-mcp 도구 (docs/MCP.md 3절). 도구와 내부 API 대응은 docs/specs/internal-api.yaml 대응표를 따른다.

- 위험 판정·변경 등급·승인 순서 확인 같은 업무 규칙은 백엔드가 계산해서 돌려준 값을 그대로 전달한다.
- 결과 필드는 계약 그대로 camelCase다. Harness의 기사 범위 제한(`truckId`)과 인자 검증(`candidateId`, `stationId`)이
  이 이름을 읽는다.
- 변경 도구의 `approval_id`·`task_id`는 Harness가 모델에게서 숨기고 승인된 호출에만 채우는 인자다 (harness.yaml
  `guardrails.injected_args`). 값이 없으면 실행하지 않는다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable
from datetime import datetime
from typing import Annotated, Any, Literal, TypeVar

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers
from pydantic import Field

from .errors import InternalApiError, from_internal, tool_error
from .internal import InternalApi

Json = dict[str, Any]
T = TypeVar("T")

INSTRUCTIONS = """\
따릉이 재배치 업무 도구. 모든 숫자(재고, 대수, 경로, 우선순위)는 백엔드·최적화기가 계산한 값이며 직접 만들지 않는다.
단위: 자전거 수는 대, 시간 길이는 H(시간)·Minutes(분), 시각은 KST 오프셋을 포함한 ISO 8601.
변경 도구(add_constraint, submit_plan, propose_route_change)는 사람이 승인한 뒤에만 실행된다.
오류는 JSON(code, message, retryable, hint)으로 온다. retryable이 true면 hint의 값으로 인자를 고쳐 다시 호출한다.
"""

READ = {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False}
COMPUTE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
WRITE = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False}

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

ConstraintType = Literal["TARGET_ADJUST", "STATION_SUSPENDED", "STATION_BLOCKED", "INVENTORY_OVERRIDE", "PRIORITY_BOOST"]
StationId = Annotated[str, Field(pattern=r"^(ST-)?\d+$",
                                 description="대여소 ID, 예: ST-1023. 대여소번호(예 108) 형식도 받는다 (docs/data/agreements.md 1번 합의 전)")]
RegionId = Annotated[str, Field(pattern=r"^[A-Za-z0-9-]+$", description="권역 ID, 예: S05, gwangjin-seongdong")]
ShiftId = Annotated[
    str,
    Field(pattern=r"^[A-Za-z0-9-]+:\d{4}-\d{2}-\d{2}:(AM|PM|EVE)$",
          description="근무조 ID '{regionId}:{yyyy-MM-dd}:{AM|PM|EVE}', 예: S05:2026-10-21:AM"),
]
# Harness가 채우는 인자. 모델에게 보이는 스키마에서는 Harness가 지운다.
InjectedApprovalId = Annotated[str | None, Field(description="승인된 마지막 카드의 approvalId (Harness가 채움)")]
InjectedTaskId = Annotated[str | None, Field(description="작업 taskId (Harness가 채움)")]


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


def _approved_change(approval_id: str | None, task_id: str | None) -> Json:
    if not (approval_id and task_id and UUID_RE.match(approval_id) and UUID_RE.match(task_id)):
        raise tool_error(
            "APPROVAL_REQUIRED",
            "승인 정보가 없습니다. 사람이 승인하면 에이전트가 승인 번호를 채워 다시 호출합니다.",
            retryable=False,
        )
    return {"approvalId": approval_id, "taskId": task_id}


# Harness는 도구 결과를 tools.result_max_chars(4,000자)에서 글자 단위로 자른다. JSON 중간이 잘리면 Harness가 결과를
# 파싱하지 못해 candidateId·planId 검증이 거부되므로, MCP가 그보다 작게(기본 3,800자) 맞춰서 보낸다.
DEFAULT_MAX_CHARS = 3800
RISK_FETCH_LIMIT = 200  # internal-api /internal/stations/risk limit 최댓값
# 계획이 길면 정류장의 이 필드부터 뺀다 (순서대로). 정류장 ID·행동·대수·적재량·상태는 남긴다.
STOP_OPTIONAL_FIELDS = ("stationName", "eta")  # 계약 Stop의 선택 필드


def _size(payload: Any) -> int:
    """Harness와 같은 방식(json.dumps, ensure_ascii=False)으로 잰 글자 수."""
    return len(json.dumps(payload, ensure_ascii=False))


def _fit(payload: Json, list_key: str, max_chars: int) -> Json:
    """결과 JSON이 max_chars를 넘으면 목록 뒤쪽부터 잘라 내고 truncated·total을 단다 (Harness Tools)."""
    items = payload[list_key]
    payload.setdefault("truncated", False)
    payload.setdefault("total", len(items))
    while len(items) > 1 and _size(payload) > max_chars:
        items.pop()
        payload["truncated"] = True
    return payload


def _mark_diffs(diffs: list[Json]) -> None:
    """경로 차이에 빠지는·더해지는 정류장 ID를 덧붙인다. before/after는 계약 RouteDiff대로 Stop 객체 그대로 둔다
    (Harness가 이 diff를 ROUTE_CHANGE 승인 카드에 그대로 싣고, 화면은 Stop의 stationId·action·bikes를 읽는다)."""
    for d in diffs:
        before = [s.get("stationId") for s in d.get("before") or []]
        after = [s.get("stationId") for s in d.get("after") or []]
        d["removed"] = [x for x in before if x not in after]
        d["added"] = [x for x in after if x not in before]


def _fit_plan(payload: Json, max_chars: int, plan_key: str | None = None) -> Json:
    """계획(또는 후보) 결과를 max_chars 안으로 줄인다. 넘칠 때만, 아래 순서로 하나씩 줄이고 무엇을 줄였는지 남긴다.

    1. 정류장(계획 경로와 diffs 모두)의 stationName, eta를 뺀다
    2. 계획 경로에서 끝난(DONE) 정류장을 뺀다 (diffs는 그대로)
    3. 정류장의 reason, loadAfter를 뺀다
    4. unserved 뒤쪽을 잘라 unservedTotal을 남기고, explanation을 200자로 줄인다
    5. 마지막으로 가장 긴 계획 경로의 뒤쪽 정류장을 자른다 (truncated, totalStops). diffs의 정류장은 자르지 않는다
    정류장 객체의 계약 필수 필드(seq, stationId, action, bikes)는 끝까지 남는다.
    diffs의 정류장은 승인 카드에 그대로 실리므로 자르지 않는다. 그래서 바뀐 정류장이 아주 많으면 max_chars를 넘을 수 있다.
    """
    plan = payload[plan_key] if plan_key else payload
    if payload.get("diffs"):
        _mark_diffs(payload["diffs"])
    routes = plan.get("routes") or []
    total = sum(len(r.get("stops") or []) for r in routes)
    omitted: list[str] = []

    def all_stops():
        diff_stops = [s for d in payload.get("diffs") or [] for s in (d.get("before") or []) + (d.get("after") or [])]
        return [s for r in routes for s in r.get("stops") or []] + diff_stops

    def drop_fields(*fields: str) -> None:
        for s in all_stops():
            for f in fields:
                s.pop(f, None)

    steps = (
        ("stationName,eta", lambda: drop_fields(*STOP_OPTIONAL_FIELDS)),
        ("doneStops", lambda: [r.__setitem__("stops", [s for s in r.get("stops") or [] if s.get("status") != "DONE"]) for r in routes]),
        ("reason,loadAfter", lambda: drop_fields("reason", "loadAfter")),
    )
    for name, step in steps:
        if _size(payload) <= max_chars:
            break
        step()
        omitted.append(name)
    if _size(payload) > max_chars:
        unserved = plan.get("unserved") or []
        plan["unservedTotal"] = len(unserved)
        while unserved and _size(payload) > max_chars:
            unserved.pop()
        if isinstance(plan.get("explanation"), str) and len(plan["explanation"]) > 200:
            plan["explanation"] = plan["explanation"][:200] + "…"
        omitted.append("unserved,explanation")
    while _size(payload) > max_chars and any(r.get("stops") for r in routes):
        longest = max(routes, key=lambda r: len(r.get("stops") or []))
        longest["stops"].pop()
        plan["truncated"] = True
    if omitted:
        plan["omitted"] = omitted
    if omitted or plan.get("truncated"):
        plan["totalStops"] = total
    return payload


def build_server(internal: InternalApi, *, max_chars: int = DEFAULT_MAX_CHARS) -> FastMCP:
    mcp = FastMCP("business-mcp", instructions=INSTRUCTIONS, version="0.1.0", mask_error_details=True)

    async def call(awaitable: Awaitable[T]) -> T:
        try:
            return await awaitable
        except InternalApiError as err:
            raise from_internal(err) from None

    # ── 조회 (readOnlyHint) ─────────────────────────────────

    @mcp.tool(annotations=READ, tags={"read"})
    async def get_risk_stations(
        region_id: RegionId,
        horizon_h: Annotated[int, Field(ge=1, le=5, description="몇 시간 앞까지 볼지(시간). 근거 문서상 3~5시간 예측이 검증 범위")] = 3,
        risk: Annotated[Literal["EMPTY", "OVERFLOW", "ALL"], Field(description="EMPTY = 빈 대여소 위험, OVERFLOW = 과적재")] = "ALL",
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
    ) -> Json:
        """권역에서 horizon_h시간 안에 빈 대여소 위험 또는 과적재가 예상되는 대여소를 우선순위 높은 순으로 돌려준다.

        반환: stations[] (stationId, name, bikes(대, 현재 재고), forecastBikes(대, horizon_h 뒤 예상 재고),
        docks(거치대 수), risk, role, neededBikes(대, 양수 회수·음수 배치), priority, emptyAt(예상 빈 시각),
        reasons[], observedAt).
        """
        # 위험 종류로 거를 때는 넉넉히 받아(계약 최대 200) 거른 뒤 limit으로 자른다. 자른 뒤 거르면 결과가 비기 쉽다.
        rows = await call(internal.risk_stations(region_id, horizon_h, RISK_FETCH_LIMIT if risk != "ALL" else limit))
        if risk != "ALL":
            rows = [r for r in rows if r.get("risk") == risk]
        payload = {"source": "GET /internal/stations/risk", "horizonH": horizon_h, "stations": rows[:limit],
                   "truncated": len(rows) > limit, "total": len(rows)}
        return _fit(payload, "stations", max_chars)

    @mcp.tool(annotations=READ, tags={"read"})
    async def get_station_detail(station_id: StationId) -> Json:
        """대여소 한 곳의 현재 상태와 근거 데이터.

        반환: docks(거치대 수), bikes(대), observedAt(재고 관측 시각), forecastBikes(대), risk,
        role(FIXED_PICKUP·FIXED_DROPOFF·VARIABLE), elevationM(m), district, recentReports[](actualBikes(대)).
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

        반환: forecasts[] (stationId, start, horizonH(시간), rentals(대, 구간 동안 나갈 자전거),
        returns(대, 구간 동안 들어올 자전거), modelVersion). 순유입은 returns - rentals.
        """
        rows = await call(internal.forecasts(station_ids, _check_dt("start", start), horizon_h))
        return _fit({"source": "GET /internal/forecasts", "forecasts": rows}, "forecasts", max_chars)

    @mcp.tool(annotations=READ, tags={"read"})
    async def get_current_plan(shift_id: ShiftId) -> Json:
        """근무조의 현재 계획.

        반환: planId, version, status(DRAFT·PROPOSED·ACTIVE·REJECTED·CLOSED), provisional(임시안 여부),
        routes[] (truckId, capacity(대), startLoad(대), stops[] (seq, stationId, action(PICKUP 회수·DROPOFF 배치),
        bikes(대), loadAfter(대), eta, reason, status(PENDING·DONE·SKIPPED))), unserved[] (stationId, priority, reason),
        explanation. 결과가 3,800자를 넘으면 정류장 이름·도착 시각, 끝난 정류장, 사유·적재량, 포기 대여소 뒤쪽·긴 설명 순으로
        줄이고, 그래도 길면 뒤쪽 정류장을 잘라 omitted·truncated·totalStops로 알린다.
        """
        plan = await call(internal.current_plan(shift_id))
        return _fit_plan({"source": f"GET /internal/shifts/{shift_id}/plan", **plan}, max_chars)

    @mcp.tool(annotations=READ, tags={"read"})
    async def get_truck_status(
        shift_id: ShiftId,
        truck_id: Annotated[str | None, Field(description="트럭 ID, 예: TR-01. 비우면 전체")] = None,
    ) -> Json:
        """트럭별 현재 위치·적재량·남은 정류장.

        반환: trucks[] (truckId, capacity(대), load(대, 현재 적재), lat, lon, shiftEnd(근무 종료 시각), remainingStops[]).
        """
        trucks = await call(internal.truck_status(shift_id))
        if truck_id:
            ids = [t["truckId"] for t in trucks]
            trucks = [t for t in trucks if t["truckId"] == truck_id]
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

        반환: reports[] (reportId, truckId, driver(마스킹), stationId, actualBikes(대, 도착 때 실제 재고),
        issue(BLOCKED·CONSTRUCTION·INVENTORY_MISMATCH·OTHER), memo, reportedAt).
        """
        rows = await call(internal.field_reports(shift_id, _check_dt("since", since) if since else None))
        rows = list(reversed(rows))  # backend는 보고 순서대로 준다 (계약). 최신순으로 뒤집는다
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

        "오늘 이 구역 회수가 왜 많지?" 같은 질의의 근거로 쓴다 (PRD 6.7, FR-08). 계약 반영 전이다 (docs/ISSUES.md ISS-06).
        반환: events[] (auditId, occurredAt, type, actor(role, display), targetId, detail).
        """
        rows = await call(internal.audit_events(shift_id, _check_dt("since", since) if since else None))
        rows = list(reversed(rows))  # backend는 기록 순서대로 준다 (계약). 최신순으로 뒤집는다
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
        task_id: InjectedTaskId = None,
    ) -> Json:
        """경로 계산을 요청하고 아직 활성화되지 않은 후보 계획을 돌려준다. 계산은 최적화기가 한다 (최대 약 60초).

        현장 실제 재고는 기사 보고로 백엔드에 이미 반영돼 있으므로 따로 넘기지 않는다.
        반환: candidateId(경로 변경 적용 때 넘김), status(OK 또는 TIMEOUT_PROVISIONAL = 시간 초과로 만든 임시안),
        plan(get_current_plan과 같은 형식), grade(활성 계획 대비 변경 등급 NONE·MINOR·MAJOR, 백엔드 규칙),
        requiredApprovers(승인 순서: NONE [], MINOR [DRIVER], MAJOR [MANAGER, DRIVER]),
        diffs[] (truckId, before[]·after[] 정류장(Stop), removed[]·added[] 정류장 ID, addedMinutes(분)).
        결과가 길면 정류장 이름·도착 시각부터 빼고 plan.omitted·truncated·totalStops로 알린다.
        """
        request: Json = {"shiftId": shift_id, "kind": kind}
        if truck_ids:
            request["truckIds"] = truck_ids
        if exclude_station_ids:
            request["excludeStationIds"] = exclude_station_ids
        # Harness는 승인 규칙이 있는 도구에만 task_id 인자를 채운다. 계산 도구는 X-Task-Id 헤더로도 받는다.
        task_id = task_id or get_http_headers(include={"x-task-id"}).get("x-task-id")
        if task_id and UUID_RE.match(task_id):
            request["requestedBy"] = {"taskId": task_id}
        candidate = await call(internal.solve(request))
        result = {"source": "POST /internal/plans/solve", **candidate}
        if candidate.get("status") == "TIMEOUT_PROVISIONAL":
            result["notice"] = "최적화 시간이 초과돼 탐욕 휴리스틱으로 만든 임시안입니다. 화면에 임시안으로 표시하세요."
        return _fit_plan(result, max_chars, plan_key="plan")

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
        approval_id: InjectedApprovalId = None,
        task_id: InjectedTaskId = None,
    ) -> Json:
        """[승인 필요] 제약을 근무조에 저장한다. 다음 계산부터 반영된다. 관리자 카드 한 장.

        STATION_SUSPENDED(대여·반납 중지)는 그 기간 수요를 0으로 보고 배치 대상에서 뺀다.
        STATION_BLOCKED(트럭 진입 불가)는 경로에서 뺀다.
        반환: constraintId와 저장된 제약.
        """
        start, end = _check_dt("valid_from", valid_from), _check_dt("valid_to", valid_to)
        if datetime.fromisoformat(start) >= datetime.fromisoformat(end):
            raise tool_error("INVALID_ARGUMENT", "valid_from은 valid_to보다 앞이어야 합니다.", retryable=True,
                             hint={"valid_from": start, "valid_to": end})
        if type in ("TARGET_ADJUST", "INVENTORY_OVERRIDE") and value is None:
            raise tool_error("INVALID_ARGUMENT", f"{type} 제약에는 value(대)가 필요합니다.", retryable=True,
                             hint={"required": "value"})
        request = {**_approved_change(approval_id, task_id), "shiftId": shift_id, "type": type,
                   "stationIds": station_ids, "value": value, "validFrom": start, "validTo": end, "reason": reason}
        saved = await call(internal.add_constraint(request))
        return {"source": "POST /internal/constraints", **saved}

    @mcp.tool(annotations=WRITE, tags={"write"})
    async def submit_plan(
        plan_id: Annotated[str, Field(description="활성화할 계획 ID (request_route_plan 결과의 plan.planId)")],
        approval_id: InjectedApprovalId = None,
        task_id: InjectedTaskId = None,
    ) -> Json:
        """[승인 필요] 관리자가 승인한 계획을 활성화한다. 관리자 카드 한 장. 승인 기록과 감사 로그는 백엔드가 남긴다.

        반환: 활성화된 계획(status=ACTIVE). 더 새로운 계획이 있으면 PLAN_SUPERSEDED와 최신 계획 ID가 온다.
        """
        plan = await call(internal.activate_plan(plan_id, _approved_change(approval_id, task_id)))
        return _fit_plan({"source": f"POST /internal/plans/{plan_id}/activate", **plan}, max_chars)

    @mcp.tool(annotations=WRITE, tags={"write"})
    async def propose_route_change(
        truck_id: Annotated[str, Field(description="대상 트럭, 예: TR-01")],
        candidate_id: Annotated[str, Field(description="request_route_plan(kind=REPLAN) 결과의 candidateId")],
        approval_id: InjectedApprovalId = None,
        task_id: InjectedTaskId = None,
    ) -> Json:
        """[승인 필요] 경로 변경 후보를 트럭에 적용한다. 승인 카드가 곧 기사 수락이다 (따로 수락 단계 없음).

        카드 순서는 후보의 requiredApprovers를 따른다: MINOR는 기사 카드 한 장, MAJOR는 관리자 → 기사 두 장.
        approval_id는 마지막 카드(기사 수락)이고, 백엔드가 순서 전체가 승인됐는지 확인한다. grade가 NONE이면 부르지 않는다.
        반환: 적용된 트럭 경로(truckId, stops[]). 더 새로운 후보가 있으면 CANDIDATE_SUPERSEDED와 최신 후보 ID가 온다.
        """
        request = {**_approved_change(approval_id, task_id), "candidateId": candidate_id}
        route = await call(internal.apply_route(truck_id, request))
        return {"source": f"POST /internal/routes/{truck_id}/apply", **route}

    return mcp
