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
from business_mcp.internal import HttpInternalApi
from business_mcp.server import build_server, to_snake

READ_TOOLS = {"get_risk_stations", "get_station_detail", "get_forecast", "get_current_plan",
              "get_truck_status", "get_field_reports", "get_audit"}
WRITE_TOOLS = {"add_constraint", "submit_plan", "propose_route_change"}
TASK_ID = str(uuid.UUID(int=1))


class Approval:
    """Harness가 붙이는 X-Approval-Id·X-Task-Id 헤더의 대역."""

    def __init__(self) -> None:
        self.approval_id: str | None = None
        self.task_id: str | None = TASK_ID

    def __call__(self) -> tuple[str | None, str | None]:
        return self.approval_id, self.task_id


@pytest.fixture
def fake() -> FakeInternalApi:
    return FakeInternalApi()


@pytest.fixture
def approval() -> Approval:
    return Approval()


@pytest.fixture
def client(fake, approval) -> Client:
    return Client(build_server(fake, approval=approval))


def approve(fake: FakeInternalApi, approval: Approval) -> str:
    """사람이 승인한 상태를 만든다: 백엔드에 승인 기록 + 에이전트가 헤더에 approvalId를 붙임."""
    approval.approval_id = str(uuid.uuid4())
    fake.approve(approval.approval_id)
    return approval.approval_id


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
        assert not {"approval_id", "task_id"} & set(tools[name].input_schema["properties"])
    assert tools["request_route_plan"].annotations.destructive_hint is False


async def test_docstrings_state_units(client):
    async with client:
        tools = {t.name: t for t in await client.list_tools()}
    assert "(대" in tools["get_forecast"].description
    assert "added_minutes(분)" in tools["request_route_plan"].description


def test_to_snake_converts_keys_only():
    assert to_snake({"truckId": "TR-01", "remainingStops": [{"stationId": "ST-1", "action": "PICKUP"}]}) == {
        "truck_id": "TR-01", "remaining_stops": [{"station_id": "ST-1", "action": "PICKUP"}]}


# ── 조회 ─────────────────────────────────────────────────

async def test_risk_stations_sorted_filtered_snake_case(client):
    async with client:
        all_rows = await call(client, "get_risk_stations", {"region_id": REGION_ID})
        empty = await call(client, "get_risk_stations", {"region_id": REGION_ID, "risk": "EMPTY"})
    priorities = [s["priority"] for s in all_rows["stations"]]
    assert priorities == sorted(priorities, reverse=True)
    assert {s["risk"] for s in empty["stations"]} == {"EMPTY"}
    assert all(s["needed_bikes"] < 0 and "forecast_bikes" in s for s in empty["stations"])
    assert all_rows["source"] == "GET /internal/stations/risk"


async def test_unknown_region_gives_candidates(client):
    async with client:
        err = await call_error(client, "get_risk_stations", {"region_id": "gangnam"})
    assert err["code"] == "REGION_NOT_FOUND" and err["hint"]["candidates"] == [REGION_ID]


async def test_station_not_found_then_retry_with_candidate(client):
    """완료 기준: 잘못된 인자에 대한 오류 메시지를 보고 다시 호출한다."""
    async with client:
        err = await call_error(client, "get_station_detail", {"station_id": "ST-9999"})
        assert err["code"] == "STATION_NOT_FOUND" and err["retryable"] is True
        retry = await call(client, "get_station_detail", {"station_id": err["hint"]["candidates"][0]})
    assert retry["station_id"] == err["hint"]["candidates"][0]
    assert "recent_reports" in retry


async def test_schema_rejects_bad_ids(client):
    async with client:
        bad_station = await client.call_tool("get_station_detail", {"station_id": "1023"}, raise_on_error=False)
        bad_shift = await client.call_tool("get_current_plan", {"shift_id": "gwangjin:2026-10-21:am"},
                                           raise_on_error=False)
    assert bad_station.is_error and bad_shift.is_error


async def test_forecast(client):
    async with client:
        res = await call(client, "get_forecast", {"station_ids": ["ST-1023"], "start": "2026-10-21T07:30:00+09:00"})
    row = res["forecasts"][0]
    assert row["start"] == "2026-10-21T07:00:00+09:00" and row["horizon_h"] == 3
    assert row["rentals"] > row["returns"]


async def test_forecast_horizon_above_5_is_rejected(client):
    async with client:
        res = await client.call_tool("get_forecast", {"station_ids": ["ST-1023"], "start": "2026-10-21T07:00:00+09:00",
                                                      "horizon_h": 6}, raise_on_error=False)
    assert res.is_error and "less than or equal to 5" in res.content[0].text


async def test_forecast_requires_timezone(client):
    async with client:
        err = await call_error(client, "get_forecast", {"station_ids": ["ST-1023"], "start": "2026-10-21T07:00:00"})
    assert err["code"] == "INVALID_ARGUMENT" and "example" in err["hint"]


async def test_current_plan_and_unknown_shift(client):
    async with client:
        plan = await call(client, "get_current_plan", {"shift_id": SHIFT_ID})
        err = await call_error(client, "get_current_plan", {"shift_id": f"{REGION_ID}:2026-10-21:PM"})
    assert plan["status"] == "ACTIVE" and plan["unserved"][0]["station_id"] == "ST-1188"
    assert plan["routes"][0]["truck_id"] == "TR-01"
    assert err["code"] == "SHIFT_NOT_FOUND" and err["hint"]["candidates"] == [SHIFT_ID]


async def test_truck_status_uses_truck_id_key_and_unknown_truck(client):
    """Harness 기사 범위 제한은 `truck_id` 키로 다른 트럭 항목을 지운다."""
    async with client:
        one = await call(client, "get_truck_status", {"shift_id": SHIFT_ID, "truck_id": "TR-01"})
        err = await call_error(client, "get_truck_status", {"shift_id": SHIFT_ID, "truck_id": "TR-09"})
    assert one["trucks"][0]["truck_id"] == "TR-01"
    assert [s["station_id"] for s in one["trucks"][0]["remaining_stops"]] == ["ST-2240", "ST-1077"]
    assert err["code"] == "TRUCK_NOT_FOUND" and "TR-01" in err["hint"]["candidates"]


async def test_field_reports_latest_first_with_limit(client):
    async with client:
        res = await call(client, "get_field_reports", {"shift_id": SHIFT_ID, "limit": 2})
        since = await call(client, "get_field_reports", {"shift_id": SHIFT_ID, "since": "2026-10-21T07:40:00+09:00"})
    assert [r["report_id"] for r in res["reports"]] == ["FR-883", "FR-881"]
    assert res["truncated"] is True and res["total"] == 3
    assert {r["report_id"] for r in since["reports"]} == {"FR-883", "FR-881"}
    assert all(r["driver"].startswith("기사") and "actual_bikes" in r for r in res["reports"])


async def test_audit_records_changes(client, fake, approval):
    async with client:
        before = await call(client, "get_audit", {"shift_id": SHIFT_ID})
        cand = await call(client, "request_route_plan", {"shift_id": SHIFT_ID})
        approve(fake, approval)
        await call(client, "submit_plan", {"plan_id": cand["plan"]["plan_id"]})
        after = await call(client, "get_audit", {"shift_id": SHIFT_ID})
    assert before["total"] == 2
    assert after["events"][0]["type"] == "PLAN_ACTIVATED" and after["events"][0]["target_id"] == cand["plan"]["plan_id"]
    assert after["source"] == f"GET /internal/shifts/{SHIFT_ID}/audit"


async def test_long_results_are_truncated_to_budget(fake):
    async with Client(build_server(fake, max_chars=900)) as c:
        res = await call(c, "get_risk_stations", {"region_id": REGION_ID})
    assert res["truncated"] is True
    assert len(json.dumps(res, ensure_ascii=False)) <= 900


# ── 계산 ─────────────────────────────────────────────────

async def test_replan_excludes_station_and_grades(client):
    async with client:
        cand = await call(client, "request_route_plan", {
            "shift_id": SHIFT_ID, "kind": "REPLAN", "truck_ids": ["TR-01"], "exclude_station_ids": ["ST-2240"],
        })
    tr01 = next(r for r in cand["plan"]["routes"] if r["truck_id"] == "TR-01")
    assert "ST-2240" not in [s["station_id"] for s in tr01["stops"]]
    assert cand["status"] == "OK" and cand["grade"] == "MINOR" and cand["plan"]["status"] == "DRAFT"
    assert cand["diffs"][0]["truck_id"] == "TR-01"
    assert any(u["station_id"] == "ST-2240" and u["reason"] == "EXCLUDED" for u in cand["plan"]["unserved"])


async def test_plan_kind_has_no_grade(client):
    async with client:
        cand = await call(client, "request_route_plan", {"shift_id": SHIFT_ID})
    assert cand["grade"] is None and cand["candidate_id"].startswith("CAND-")


# ── 변경 (승인 헤더) ─────────────────────────────────────

CONSTRAINT = {"shift_id": SHIFT_ID, "type": "STATION_SUSPENDED", "station_ids": ["ST-1023"],
              "valid_from": "2026-10-21T09:00:00+09:00", "valid_to": "2026-10-21T22:00:00+09:00",
              "reason": "행사로 대여·반납 중지"}


async def test_write_without_approval_header_is_rejected(client, approval):
    approval.task_id = None
    async with client:
        err = await call_error(client, "add_constraint", CONSTRAINT)
    assert err["code"] == "APPROVAL_REQUIRED" and err["retryable"] is False


async def test_write_with_unapproved_id_is_rejected_by_backend(client, approval):
    approval.approval_id = str(uuid.uuid4())  # 헤더는 있지만 백엔드에 승인 기록이 없음
    async with client:
        err = await call_error(client, "add_constraint", CONSTRAINT)
    assert err["code"] == "APPROVAL_REQUIRED" and err["retryable"] is False


async def test_add_constraint_with_approval_and_replay(client, fake, approval):
    approve(fake, approval)
    async with client:
        saved = await call(client, "add_constraint", CONSTRAINT)
        again = await call(client, "add_constraint", CONSTRAINT)
        reused = await call_error(client, "add_constraint", {**CONSTRAINT, "station_ids": ["ST-1031"]})
    assert saved["constraint_id"] == again["constraint_id"]
    assert reused["code"] == "APPROVAL_ALREADY_USED"


async def test_add_constraint_argument_checks(client, fake, approval):
    approve(fake, approval)
    async with client:
        bad_period = await call_error(client, "add_constraint", {
            **CONSTRAINT, "valid_from": "2026-10-21T22:00:00+09:00", "valid_to": "2026-10-21T09:00:00+09:00"})
        missing_value = await call_error(client, "add_constraint", {**CONSTRAINT, "type": "TARGET_ADJUST"})
    assert bad_period["code"] == missing_value["code"] == "INVALID_ARGUMENT"


async def test_submit_plan_superseded_then_latest(client, fake, approval):
    async with client:
        current = await call(client, "get_current_plan", {"shift_id": SHIFT_ID})
        cand = await call(client, "request_route_plan", {"shift_id": SHIFT_ID})
        approve(fake, approval)
        err = await call_error(client, "submit_plan", {"plan_id": current["plan_id"]})
        assert err["code"] == "PLAN_SUPERSEDED" and err["hint"]["candidates"] == [cand["plan"]["plan_id"]]
        approve(fake, approval)
        ok = await call(client, "submit_plan", {"plan_id": err["hint"]["candidates"][0]})
    assert ok["status"] == "ACTIVE"


async def test_propose_route_change_applies_latest_candidate_only(client, fake, approval):
    async with client:
        old = await call(client, "request_route_plan", {"shift_id": SHIFT_ID, "kind": "REPLAN",
                                                        "exclude_station_ids": ["ST-2240"]})
        new = await call(client, "request_route_plan", {"shift_id": SHIFT_ID, "kind": "REPLAN",
                                                        "exclude_station_ids": ["ST-1077"]})
        approve(fake, approval)
        stale = await call_error(client, "propose_route_change", {"truck_id": "TR-01", "candidate_id": old["candidate_id"]})
        approve(fake, approval)
        route = await call(client, "propose_route_change", {"truck_id": "TR-01", "candidate_id": new["candidate_id"]})
    assert stale["code"] == "CANDIDATE_SUPERSEDED" and stale["hint"]["candidates"] == [new["candidate_id"]]
    assert route["truck_id"] == "TR-01"


# ── HTTP 어댑터 (백엔드 대역) ────────────────────────────

async def test_http_adapter_paths_token_body_and_problem_mapping(approval):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        seen.append((req.method, req.url.path, dict(req.url.params), req.headers.get("X-Internal-Token"), body))
        if req.url.path == "/internal/stations/ST-9999":
            return httpx.Response(404, json={"type": "about:blank", "title": "대여소 없음", "status": 404,
                                             "code": "STATION_NOT_FOUND", "detail": "없음", "suggestions": ["ST-1023"]})
        if req.url.path == "/internal/constraints":
            return httpx.Response(403, json={"type": "about:blank", "title": "승인되지 않은 변경", "status": 403,
                                             "code": "APPROVAL_REQUIRED", "detail": "PENDING 상태"})
        if req.url.path == "/internal/plans/solve":
            return httpx.Response(503, headers={"Retry-After": "7"})
        if req.url.path == "/internal/stations/risk":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"stationId": "ST-1023"})

    api = HttpInternalApi("http://backend:8080", token="t0k", transport=httpx.MockTransport(handler))
    approval.approval_id = str(uuid.uuid4())
    async with Client(build_server(api, approval=approval)) as c:
        ok = await call(c, "get_station_detail", {"station_id": "ST-1023"})
        await call(c, "get_risk_stations", {"region_id": REGION_ID, "horizon_h": 4})
        nf = await call_error(c, "get_station_detail", {"station_id": "ST-9999"})
        denied = await call_error(c, "add_constraint", CONSTRAINT)
        down = await call_error(c, "request_route_plan", {"shift_id": SHIFT_ID})
    await api.aclose()
    assert ok["station_id"] == "ST-1023"
    assert nf["code"] == "STATION_NOT_FOUND" and nf["hint"]["candidates"] == ["ST-1023"] and nf["retryable"]
    assert denied["code"] == "APPROVAL_REQUIRED" and denied["retryable"] is False
    assert down["code"] == "UPSTREAM_UNAVAILABLE" and down["hint"]["retry_after_s"] == 7
    assert ("GET", "/internal/stations/risk", {"regionId": REGION_ID, "horizonH": "4", "limit": "20"}, "t0k", None) in seen
    constraint_body = next(s[4] for s in seen if s[1] == "/internal/constraints")
    assert constraint_body["approvalId"] == approval.approval_id and constraint_body["taskId"] == TASK_ID
    assert constraint_body["stationIds"] == ["ST-1023"]
