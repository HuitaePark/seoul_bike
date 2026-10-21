# Author: 정유은
# Date: 20OCT2026
# Purpose: business-mcp 도구 단위 테스트. 인메모리 Client로 MCP 프로토콜을 그대로 거치고, 가짜 백엔드는 specs 형식을 따른다.

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from fastmcp import Client

from business_mcp.fake_internal import REGION_ID, SHIFT_ID, FakeInternalApi
from business_mcp.errors import InternalApiError
from business_mcp.internal import HTTP_READY_METHODS, HttpInternalApi, RoutedInternalApi
from business_mcp.server import build_server

READ_TOOLS = {"get_risk_stations", "get_station_detail", "get_forecast", "get_current_plan",
              "get_truck_status", "get_field_reports", "get_audit"}
WRITE_TOOLS = {"add_constraint", "submit_plan", "propose_route_change"}
INJECTED = {"approval_id", "task_id"}
TASK_ID = str(uuid.UUID(int=1))


@pytest.fixture
def fake() -> FakeInternalApi:
    return FakeInternalApi()


@pytest.fixture
def client(fake) -> Client:
    return Client(build_server(fake))


def card(fake: FakeInternalApi, role: str = "MANAGER", candidate_id: str | None = None) -> dict:
    """사람이 카드를 승인한 상태를 만들고, Harness가 채우는 인자를 돌려준다."""
    approval_id = str(uuid.uuid4())
    fake.approve(approval_id, role=role, task_id=TASK_ID, candidate_id=candidate_id)
    return {"approval_id": approval_id, "task_id": TASK_ID}


async def call(client: Client, name: str, args: dict) -> dict:
    res = await client.call_tool(name, args)
    return res.structured_content


async def call_error(client: Client, name: str, args: dict) -> dict:
    res = await client.call_tool(name, args, raise_on_error=False)
    assert res.is_error
    return json.loads(res.content[0].text)


# ── 도구 목록과 힌트 ─────────────────────────────────────

async def test_lists_eleven_tools_with_hints(client):
    async with client:
        tools = {t.name: t for t in await client.list_tools()}
    assert set(tools) == READ_TOOLS | WRITE_TOOLS | {"request_route_plan"}
    for name in READ_TOOLS:
        assert tools[name].annotations.read_only_hint is True
    for name in WRITE_TOOLS:
        assert tools[name].annotations.destructive_hint is True
        assert "[승인 필요]" in tools[name].description
    assert tools["request_route_plan"].annotations.destructive_hint is False


async def test_injected_args_are_in_schema_but_optional(client):
    """Harness hide_injected_args는 스키마에 있는 approval_id·task_id를 모델 시야에서 지우고, 승인된 호출에 채운다."""
    async with client:
        tools = {t.name: t for t in await client.list_tools()}
    for name in WRITE_TOOLS:
        schema = tools[name].input_schema
        assert INJECTED <= set(schema["properties"])
        assert not INJECTED & set(schema.get("required", []))
    assert "task_id" in tools["request_route_plan"].input_schema["properties"]


async def test_tool_args_match_harness_validation(client):
    """agent/harness/validation.py SCHEMAS의 인자 이름과 같다."""
    expected = {
        "request_route_plan": {"shift_id", "kind", "truck_ids", "exclude_station_ids"},
        "add_constraint": {"shift_id", "type", "station_ids", "valid_from", "valid_to", "reason", "value"},
        "submit_plan": {"plan_id"},
        "propose_route_change": {"truck_id", "candidate_id"},
    }
    async with client:
        tools = {t.name: t for t in await client.list_tools()}
    for name, args in expected.items():
        assert set(tools[name].input_schema["properties"]) - INJECTED == args


async def test_docstrings_state_units(client):
    async with client:
        tools = {t.name: t for t in await client.list_tools()}
    assert "(대" in tools["get_forecast"].description
    assert "addedMinutes(분)" in tools["request_route_plan"].description


# ── 조회 ─────────────────────────────────────────────────

async def test_risk_stations_sorted_filtered_camel_case(client):
    async with client:
        all_rows = await call(client, "get_risk_stations", {"region_id": REGION_ID})
        empty = await call(client, "get_risk_stations", {"region_id": REGION_ID, "risk": "EMPTY"})
    priorities = [s["priority"] for s in all_rows["stations"]]
    assert priorities == sorted(priorities, reverse=True)
    assert {s["risk"] for s in empty["stations"]} == {"EMPTY"}
    assert all(s["neededBikes"] < 0 and "forecastBikes" in s and "stationId" in s for s in empty["stations"])
    assert all_rows["source"] == "GET /internal/stations/risk" and all_rows["horizonH"] == 3


async def test_unknown_region_gives_candidates(client):
    async with client:
        err = await call_error(client, "get_risk_stations", {"region_id": "S05"})
    assert err["code"] == "REGION_NOT_FOUND" and err["hint"]["candidates"] == [REGION_ID]


async def test_other_region_shift_gives_candidates(client):
    """형식이 맞는 다른 구역의 근무조는 스키마를 통과하고, 백엔드가 아는 근무조를 후보로 준다."""
    async with client:
        err = await call_error(client, "get_current_plan", {"shift_id": "S05:2026-10-21:AM"})
    assert err["code"] == "SHIFT_NOT_FOUND" and err["hint"]["candidates"] == [SHIFT_ID]


async def test_station_number_and_old_region_are_rejected(client):
    """대여소 ID는 실시간 API ID(ST-숫자)만, 구역은 N01~S09만 받는다 (agreements.md 결정 1·2).
    대여소번호 102는 ST-4이고 ST-102는 번호 505라 번호를 그대로 받으면 엉뚱한 대여소가 된다."""
    async with client:
        number = await client.call_tool("get_station_detail", {"station_id": "102"}, raise_on_error=False)
        old_region = await client.call_tool("get_risk_stations", {"region_id": "gwangjin-seongdong"}, raise_on_error=False)
        old_shift = await client.call_tool("get_current_plan", {"shift_id": "gwangjin-seongdong:2026-10-21:AM"},
                                           raise_on_error=False)
    assert number.is_error and old_region.is_error and old_shift.is_error
    assert "^ST-\\d+$" in number.content[0].text and "^[NS]\\d{2}$" in old_region.content[0].text


async def test_station_not_found_then_retry_with_candidate(client):
    """완료 기준: 잘못된 인자에 대한 오류 메시지를 보고 다시 호출한다."""
    async with client:
        err = await call_error(client, "get_station_detail", {"station_id": "ST-9999"})
        assert err["code"] == "STATION_NOT_FOUND" and err["retryable"] is True
        retry = await call(client, "get_station_detail", {"station_id": err["hint"]["candidates"][0]})
    assert retry["stationId"] == err["hint"]["candidates"][0]
    assert "recentReports" in retry


async def test_schema_rejects_bad_ids(client):
    async with client:
        bad_station = await client.call_tool("get_station_detail", {"station_id": "강남-1023"}, raise_on_error=False)
        bad_shift = await client.call_tool("get_current_plan", {"shift_id": "S05:2026-10-21:am"}, raise_on_error=False)
    assert bad_station.is_error and bad_shift.is_error


async def test_forecast(client):
    async with client:
        res = await call(client, "get_forecast", {"station_ids": ["ST-3390"], "start": "2026-10-21T07:30:00+09:00"})
    row = res["forecasts"][0]
    assert row["start"] == "2026-10-21T07:00:00+09:00" and row["horizonH"] == 3
    assert row["rentals"] > row["returns"]


async def test_forecast_horizon_above_5_is_rejected(client):
    async with client:
        res = await client.call_tool("get_forecast", {"station_ids": ["ST-3390"], "start": "2026-10-21T07:00:00+09:00",
                                                      "horizon_h": 6}, raise_on_error=False)
    assert res.is_error and "less than or equal to 5" in res.content[0].text


async def test_forecast_requires_timezone(client):
    async with client:
        err = await call_error(client, "get_forecast", {"station_ids": ["ST-3390"], "start": "2026-10-21T07:00:00"})
    assert err["code"] == "INVALID_ARGUMENT" and "example" in err["hint"]


async def test_current_plan(client):
    async with client:
        plan = await call(client, "get_current_plan", {"shift_id": SHIFT_ID})
    assert plan["status"] == "ACTIVE" and plan["unserved"][0]["stationId"] == "ST-102"
    assert plan["routes"][0]["truckId"] == "TR-01"


async def test_truck_status_uses_truck_id_key_and_unknown_truck(client):
    """Harness 기사 범위 제한(scope_to_truck)은 `truckId` 키로 다른 트럭 항목을 지운다."""
    async with client:
        one = await call(client, "get_truck_status", {"shift_id": SHIFT_ID, "truck_id": "TR-01"})
        err = await call_error(client, "get_truck_status", {"shift_id": SHIFT_ID, "truck_id": "TR-09"})
    assert one["trucks"][0]["truckId"] == "TR-01"
    assert [s["stationId"] for s in one["trucks"][0]["remainingStops"]] == ["ST-97", "ST-253"]
    assert err["code"] == "TRUCK_NOT_FOUND" and "TR-01" in err["hint"]["candidates"]


async def test_field_reports_latest_first_with_limit(client):
    async with client:
        res = await call(client, "get_field_reports", {"shift_id": SHIFT_ID, "limit": 2})
        since = await call(client, "get_field_reports", {"shift_id": SHIFT_ID, "since": "2026-10-21T07:40:00+09:00"})
    assert [r["reportId"] for r in res["reports"]] == ["FR-883", "FR-881"]
    assert res["truncated"] is True and res["total"] == 3
    assert {r["reportId"] for r in since["reports"]} == {"FR-883", "FR-881"}
    assert all(r["driver"].endswith("기사") and "actualBikes" in r for r in res["reports"])


async def test_since_excludes_that_moment(client):
    """계약 0.5.0: since는 그 시각을 뺀다 (마지막으로 받은 시각을 다시 넣어도 중복이 없다)."""
    async with client:
        reports = await call(client, "get_field_reports", {"shift_id": SHIFT_ID, "since": "2026-10-21T07:41:00+09:00"})
        audit = await call(client, "get_audit", {"shift_id": SHIFT_ID, "since": "2026-10-21T07:41:00+09:00"})
    assert [r["reportId"] for r in reports["reports"]] == ["FR-883"]
    assert audit["events"] == []


async def test_audit_records_changes(client, fake):
    async with client:
        before = await call(client, "get_audit", {"shift_id": SHIFT_ID})
        cand = await call(client, "request_route_plan", {"shift_id": SHIFT_ID})
        await call(client, "submit_plan", {"plan_id": cand["plan"]["planId"], **card(fake)})
        after = await call(client, "get_audit", {"shift_id": SHIFT_ID})
    assert before["total"] == 2
    # 최신순: 계획 활성화가 맨 앞, 그 앞에 관리자 승인 기록 (계약 AuditEvent 종류)
    assert [e["type"] for e in after["events"][:2]] == ["PLAN_ACTIVATED", "APPROVAL_APPROVED"]
    assert after["events"][0]["targetId"] == cand["plan"]["planId"]


async def test_long_results_are_truncated_to_budget(fake):
    async with Client(build_server(fake, max_chars=900)) as c:
        res = await call(c, "get_risk_stations", {"region_id": REGION_ID})
    assert res["truncated"] is True
    assert len(json.dumps(res, ensure_ascii=False)) <= 900


async def test_risk_filter_is_applied_before_limit(client):
    """위험 종류로 거른 뒤 limit으로 자른다 (자른 뒤 거르면 과적재가 위에 있을 때 빈 대여소가 0곳이 된다)."""
    async with client:
        res = await call(client, "get_risk_stations", {"region_id": REGION_ID, "risk": "EMPTY", "limit": 2})
    assert len(res["stations"]) == 2 and {s["risk"] for s in res["stations"]} == {"EMPTY"}
    assert res["total"] == 5 and res["truncated"] is True


# ── 계산 ─────────────────────────────────────────────────

async def test_replan_minor_requires_driver_only(client):
    async with client:
        cand = await call(client, "request_route_plan", {
            "shift_id": SHIFT_ID, "kind": "REPLAN", "truck_ids": ["TR-01"], "exclude_station_ids": ["ST-97"],
        })
    tr01 = next(r for r in cand["plan"]["routes"] if r["truckId"] == "TR-01")
    assert "ST-97" not in [s["stationId"] for s in tr01["stops"]]
    assert cand["status"] == "OK" and cand["plan"]["status"] == "DRAFT"
    assert (cand["grade"], cand["requiredApprovers"]) == ("MINOR", ["DRIVER"])
    assert cand["diffs"][0]["truckId"] == "TR-01"
    # 관리자 제외는 openapi Unserved.reason BLOCKED (PR #34)
    assert any(u["stationId"] == "ST-97" and u["reason"] == "BLOCKED" for u in cand["plan"]["unserved"])


async def test_replan_major_requires_manager_then_driver(client):
    async with client:
        cand = await call(client, "request_route_plan", {
            "shift_id": SHIFT_ID, "kind": "REPLAN", "exclude_station_ids": ["ST-97", "ST-2309"],
        })
    assert (cand["grade"], cand["requiredApprovers"]) == ("MAJOR", ["MANAGER", "DRIVER"])


STOP_REQUIRED = {"seq", "stationId", "action", "bikes"}  # openapi Stop 필수 필드 (승인 카드 화면이 읽음)


async def test_major_replan_result_fits_harness_limit(client):
    """Harness는 4,000자에서 글자 단위로 자른다. MAJOR 후보도 3,800자 안에서 온전한 JSON이어야 한다."""
    async with client:
        cand = await call(client, "request_route_plan", {"shift_id": SHIFT_ID, "kind": "REPLAN",
                                                         "exclude_station_ids": ["ST-97", "ST-2309"]})
    assert len(json.dumps(cand, ensure_ascii=False)) <= 3800
    assert cand["candidateId"] and cand["plan"]["planId"] and cand["requiredApprovers"] == ["MANAGER", "DRIVER"]
    assert cand["plan"]["omitted"] == ["stationName,eta"] and "truncated" not in cand["plan"]  # 정류장은 자르지 않음


async def test_route_diff_keeps_stop_objects_for_approval_card(fake):
    """계약 RouteDiff: before/after는 Stop[]. 승인 카드 화면이 stationId·action·bikes를 읽는다 (PR #23 리뷰).
    실제 대여소 이름이 길어 이 후보가 기본 한도(3,800자)를 조금 넘으므로, 줄이지 않는 경우는 넉넉한 한도로 본다."""
    async with Client(build_server(fake, max_chars=8000)) as c:
        cand = await call(c, "request_route_plan", {"shift_id": SHIFT_ID, "kind": "REPLAN", "truck_ids": ["TR-01"],
                                                    "exclude_station_ids": ["ST-97"]})
    diff = cand["diffs"][0]
    assert all(isinstance(s, dict) and STOP_REQUIRED <= s.keys() for s in diff["before"] + diff["after"])
    assert diff["removed"] == ["ST-97"] and diff["added"] == []
    assert "stationName" in diff["before"][0]  # 길이가 넉넉하면 아무것도 빼지 않는다
    assert "omitted" not in cand["plan"]


async def test_plan_is_compacted_then_truncated_under_tight_budget(fake):
    async with Client(build_server(fake, max_chars=2500)) as c:
        cand = await call(c, "request_route_plan", {"shift_id": SHIFT_ID, "kind": "REPLAN",
                                                    "exclude_station_ids": ["ST-97", "ST-2309"]})
    assert len(json.dumps(cand, ensure_ascii=False)) <= 2500
    assert cand["candidateId"] and cand["plan"]["planId"]  # 검증에 필요한 ID는 끝까지 남는다
    assert cand["plan"]["omitted"][0] == "stationName,eta" and cand["plan"]["totalStops"] > 0
    assert all(STOP_REQUIRED <= s.keys() for d in cand["diffs"] for s in d["before"] + d["after"])


def test_unserved_and_explanation_are_trimmed_before_stops():
    """포기 대여소가 많으면 정류장을 자르기 전에 unserved 뒤쪽과 긴 설명부터 줄인다 (PR #23 리뷰 추정 항목)."""
    from business_mcp.server import _fit_plan

    stop = {"seq": 1, "stationId": "ST-1", "action": "PICKUP", "bikes": 3, "status": "PENDING"}
    plan = {"planId": "P-1", "routes": [{"truckId": "TR-1", "stops": [dict(stop, seq=i) for i in range(1, 6)]}],
            "unserved": [{"stationId": f"ST-{i}", "priority": 1.0, "reason": "NOT_SELECTED"} for i in range(300)],
            "explanation": "설명" * 400}
    out = _fit_plan(plan, 2000)
    assert len(json.dumps(out, ensure_ascii=False)) <= 2000
    assert out["unservedTotal"] == 300 and len(out["unserved"]) < 300
    assert len(out["routes"][0]["stops"]) == 5 and "truncated" not in out  # 정류장은 그대로


async def test_plan_kind_has_no_grade(client):
    async with client:
        cand = await call(client, "request_route_plan", {"shift_id": SHIFT_ID})
    assert cand["grade"] is None and cand["requiredApprovers"] == [] and cand["candidateId"].startswith("CAND-")


# ── 변경 (Harness가 채우는 승인 인자) ────────────────────

CONSTRAINT = {"shift_id": SHIFT_ID, "type": "STATION_SUSPENDED", "station_ids": ["ST-3390"],
              "valid_from": "2026-10-21T09:00:00+09:00", "valid_to": "2026-10-21T22:00:00+09:00",
              "reason": "행사로 대여·반납 중지"}


async def test_write_without_injected_approval_is_rejected(client):
    async with client:
        err = await call_error(client, "add_constraint", CONSTRAINT)
    assert err["code"] == "APPROVAL_REQUIRED" and err["retryable"] is False


async def test_write_with_unapproved_or_wrong_role_is_rejected_by_backend(client, fake):
    async with client:
        unapproved = await call_error(client, "add_constraint", {**CONSTRAINT, "approval_id": str(uuid.uuid4()),
                                                                 "task_id": TASK_ID})
        driver_card = await call_error(client, "add_constraint", {**CONSTRAINT, **card(fake, role="DRIVER")})
    assert unapproved["code"] == driver_card["code"] == "APPROVAL_REQUIRED"
    assert unapproved["retryable"] is False


async def test_add_constraint_with_approval_and_replay(client, fake):
    approval = card(fake)
    async with client:
        saved = await call(client, "add_constraint", {**CONSTRAINT, **approval})
        again = await call(client, "add_constraint", {**CONSTRAINT, **approval})
        reused = await call_error(client, "add_constraint", {**CONSTRAINT, **approval, "station_ids": ["ST-988"]})
    assert saved["constraintId"] == again["constraintId"]
    assert reused["code"] == "APPROVAL_ALREADY_USED"


async def test_add_constraint_argument_checks(client, fake):
    async with client:
        bad_period = await call_error(client, "add_constraint", {
            **CONSTRAINT, **card(fake), "valid_from": "2026-10-21T22:00:00+09:00", "valid_to": "2026-10-21T09:00:00+09:00"})
        missing_value = await call_error(client, "add_constraint", {**CONSTRAINT, **card(fake), "type": "TARGET_ADJUST"})
    assert bad_period["code"] == missing_value["code"] == "INVALID_ARGUMENT"


async def test_submit_plan_superseded_then_latest(client, fake):
    async with client:
        current = await call(client, "get_current_plan", {"shift_id": SHIFT_ID})
        cand = await call(client, "request_route_plan", {"shift_id": SHIFT_ID})
        err = await call_error(client, "submit_plan", {"plan_id": current["planId"], **card(fake)})
        assert err["code"] == "PLAN_SUPERSEDED" and err["hint"]["candidates"] == [cand["plan"]["planId"]]
        ok = await call(client, "submit_plan", {"plan_id": err["hint"]["candidates"][0], **card(fake)})
    assert ok["status"] == "ACTIVE"


async def test_route_change_minor_with_driver_card(client, fake):
    async with client:
        cand = await call(client, "request_route_plan", {"shift_id": SHIFT_ID, "kind": "REPLAN", "truck_ids": ["TR-01"],
                                                         "exclude_station_ids": ["ST-97"]})
        manager_only = await call_error(client, "propose_route_change", {
            "truck_id": "TR-01", "candidate_id": cand["candidateId"], **card(fake, role="MANAGER")})
        route = await call(client, "propose_route_change", {
            "truck_id": "TR-01", "candidate_id": cand["candidateId"], **card(fake, role="DRIVER")})
    assert manager_only["code"] == "APPROVAL_REQUIRED"
    assert route["truckId"] == "TR-01"


async def test_route_change_major_needs_both_cards_in_same_task(client, fake):
    async with client:
        cand = await call(client, "request_route_plan", {"shift_id": SHIFT_ID, "kind": "REPLAN",
                                                         "exclude_station_ids": ["ST-97", "ST-2309"]})
        args = {"truck_id": "TR-01", "candidate_id": cand["candidateId"]}
        driver_only = await call_error(client, "propose_route_change", {**args, **card(fake, role="DRIVER")})
        card(fake, role="MANAGER", candidate_id=cand["candidateId"])  # 관리자 카드 승인
        route = await call(client, "propose_route_change", {**args, **card(fake, role="DRIVER")})
    assert driver_only["code"] == "APPROVAL_REQUIRED" and "MANAGER" in driver_only["message"]
    assert route["truckId"] == "TR-01"


async def test_route_change_applies_latest_candidate_only(client, fake):
    async with client:
        old = await call(client, "request_route_plan", {"shift_id": SHIFT_ID, "kind": "REPLAN", "truck_ids": ["TR-01"],
                                                        "exclude_station_ids": ["ST-97"]})
        new = await call(client, "request_route_plan", {"shift_id": SHIFT_ID, "kind": "REPLAN", "truck_ids": ["TR-01"],
                                                        "exclude_station_ids": ["ST-253"]})
        stale = await call_error(client, "propose_route_change", {
            "truck_id": "TR-01", "candidate_id": old["candidateId"], **card(fake, role="DRIVER")})
    assert stale["code"] == "CANDIDATE_SUPERSEDED" and stale["hint"]["candidates"] == [new["candidateId"]]


# ── HTTP 어댑터 (백엔드 대역) ────────────────────────────

async def test_http_adapter_paths_token_body_and_problem_mapping():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        seen.append((req.method, req.url.path, dict(req.url.params), req.headers.get("X-Internal-Token"), body))
        if req.url.path == "/internal/stations/ST-9999":
            return httpx.Response(404, json={"type": "about:blank", "title": "대여소 없음", "status": 404,
                                             "code": "STATION_NOT_FOUND", "detail": "없음", "suggestions": ["ST-3390"]})
        if req.url.path == "/internal/constraints":
            return httpx.Response(403, json={"type": "about:blank", "title": "승인되지 않은 변경", "status": 403,
                                             "code": "APPROVAL_REQUIRED", "detail": "PENDING 상태"})
        if req.url.path == "/internal/plans/solve":
            return httpx.Response(503, headers={"Retry-After": "7"})
        if req.url.path == "/internal/stations/risk":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"stationId": "ST-3390"})

    api = HttpInternalApi("http://backend:8080", token="t0k", transport=httpx.MockTransport(handler))
    approval_id = str(uuid.uuid4())
    async with Client(build_server(api)) as c:
        ok = await call(c, "get_station_detail", {"station_id": "ST-3390"})
        await call(c, "get_risk_stations", {"region_id": REGION_ID, "horizon_h": 4})
        nf = await call_error(c, "get_station_detail", {"station_id": "ST-9999"})
        denied = await call_error(c, "add_constraint", {**CONSTRAINT, "approval_id": approval_id, "task_id": TASK_ID})
        down = await call_error(c, "request_route_plan", {"shift_id": SHIFT_ID, "task_id": TASK_ID})
    await api.aclose()
    assert ok["stationId"] == "ST-3390"
    assert nf["code"] == "STATION_NOT_FOUND" and nf["hint"]["candidates"] == ["ST-3390"] and nf["retryable"]
    assert denied["code"] == "APPROVAL_REQUIRED" and denied["retryable"] is False
    assert down["code"] == "UPSTREAM_UNAVAILABLE" and down["hint"]["retry_after_s"] == 7
    assert ("GET", "/internal/stations/risk", {"regionId": REGION_ID, "horizonH": "4", "limit": "20"}, "t0k", None) in seen
    constraint_body = next(s[4] for s in seen if s[1] == "/internal/constraints")
    assert constraint_body["approvalId"] == approval_id and constraint_body["taskId"] == TASK_ID
    solve_body = next(s[4] for s in seen if s[1] == "/internal/plans/solve")
    assert solve_body["requestedBy"] == {"taskId": TASK_ID}


def test_problemless_errors_get_status_codes():
    """본문 없는 오류도 상태별 코드로. 다시 부를 값이 없으면 retryable=false."""
    cases = {401: ("UNAUTHORIZED", False), 403: ("FORBIDDEN", False), 404: ("NOT_FOUND", False),
             409: ("CONFLICT", False), 400: ("INVALID_ARGUMENT", True)}
    for status, (code, retryable) in cases.items():
        err = InternalApiError.from_problem(status, {})
        assert (err.code, err.retryable) == (code, retryable), status
    with_hint = InternalApiError.from_problem(404, {"code": "STATION_NOT_FOUND", "detail": "없음", "suggestions": ["ST-1"]})
    assert with_hint.retryable is True and with_hint.hint == {"candidates": ["ST-1"]}
    assert InternalApiError.from_problem(403, {"code": "APPROVAL_REQUIRED", "suggestions": ["x"]}).retryable is False


async def test_hybrid_mode_calls_backend_only_for_opened_apis(fake):
    """INTERNAL_MODE=hybrid: backend가 연 현장 보고·제약 추가·감사 기록만 http로, 나머지는 가짜로."""
    paths = []

    def handler(req: httpx.Request) -> httpx.Response:
        paths.append(req.url.path)
        return httpx.Response(200, json=[{"reportId": "R-9", "truckId": "truck-1", "driver": "truck-1 기사",
                                          "stationId": "108", "actualBikes": 3, "reportedAt": "2026-10-21T08:00:00+09:00"}])

    http = HttpInternalApi("http://backend:8080", token="t", transport=httpx.MockTransport(handler))
    api = RoutedInternalApi(fake, {m: http for m in HTTP_READY_METHODS})
    async with Client(build_server(api)) as c:
        reports = await call(c, "get_field_reports", {"shift_id": SHIFT_ID})
        plan = await call(c, "get_current_plan", {"shift_id": SHIFT_ID})
    await http.aclose()
    assert reports["reports"][0]["reportId"] == "R-9" and paths == [f"/internal/shifts/{SHIFT_ID}/reports"]
    assert plan["planId"].startswith("PL-")  # 계획은 아직 가짜
