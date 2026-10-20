# Author: Cheon Seonghun
# Date: 20OCT2026
# Purpose: In-memory business-mcp / rag-mcp doubles that follow docs/specs internal-api, for tests and scenarios.

"""Fake MCP servers over the real MCP protocol (in-memory transport).

Tool names follow the MCP-to-internal-API table in docs/specs/internal-api.yaml; arguments are the API fields in
snake_case and results are the API schemas (camelCase). The fake also plays the backend's part of the contract:
change tools refuse calls whose `approval_id` was not approved (403 `APPROVAL_REQUIRED`), and route plan
candidates carry the change grade the backend computes. This is a test double for evaluation only; the real
servers belong to the MCP · RAG and backend roles.
"""
from __future__ import annotations

import asyncio
import copy
import itertools
import json
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import ToolAnnotations

from harness.tools import TraceHeaderInterceptor

READ = ToolAnnotations(readOnlyHint=True)
COMPUTE = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
CHANGE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)

SHIFT_ID = "yeongdeungpo:2026-10-24:PM"
OBSERVED_AT = "2026-10-24T13:00:00+09:00"
SHIFT_END = "2026-10-24T18:00:00+09:00"
RISK_KO = {"EMPTY": "빈 대여소", "OVERFLOW": "과적재"}


def _station(sid: str, name: str, lat: float, lon: float, docks: int, bikes: int, forecast: int, risk: str) -> dict:
    return {
        "stationId": sid, "name": name, "lat": lat, "lon": lon, "docks": docks, "bikes": bikes,
        "observedAt": OBSERVED_AT, "forecastBikes": forecast, "risk": risk, "role": "VARIABLE",
    }


STATIONS: dict[str, dict[str, Any]] = {
    s["stationId"]: s
    for s in [
        _station("ST-2001", "여의나루역 1번출구", 37.5271, 126.9326, 20, 18, 24, "OVERFLOW"),
        _station("ST-2002", "여의나루역 2번출구", 37.5268, 126.9341, 15, 14, 19, "OVERFLOW"),
        _station("ST-2003", "여의도공원 앞", 37.5257, 126.9223, 20, 8, 1, "EMPTY"),
        _station("ST-2004", "국회의사당역 6번출구", 37.5282, 126.9178, 15, 1, 0, "EMPTY"),
        _station("ST-2005", "여의도역 5번출구", 37.5215, 126.9243, 25, 2, 0, "EMPTY"),
        _station("ST-2006", "IFC몰 앞", 37.5251, 126.9255, 20, 19, 23, "OVERFLOW"),
        _station("ST-2007", "샛강역 1번출구", 37.5172, 126.9285, 15, 0, 0, "EMPTY"),
        _station("ST-2008", "63빌딩 앞", 37.5199, 126.9402, 15, 9, 8, "NONE"),
    ]
}

TRUCKS: dict[str, dict[str, Any]] = {
    "TR-1": {"truckId": "TR-1", "capacity": 15, "load": 0, "lat": 37.5194, "lon": 126.9403, "shiftEnd": SHIFT_END},
    "TR-2": {"truckId": "TR-2", "capacity": 15, "load": 0, "lat": 37.5251, "lon": 126.9255, "shiftEnd": SHIFT_END},
}

DRIVER_NAMES = ("김민수", "박지훈")
DRIVER_PHONES = ("010-2345-6789", "010-9876-5432")

DOCS: dict[str, dict[str, str]] = {
    "notice-2026-fireworks": {
        "source": "서울시 공지 (2026-10-20)",
        "text": (
            "2026 서울세계불꽃축제 기간(10월 24일 17:00~22:00) 안전 관리를 위해 여의나루역 1번출구(ST-2001), "
            "여의나루역 2번출구(ST-2002) 대여소의 대여·반납을 중지한다. 해당 대여소의 자전거는 16:00까지 회수한다."
        ),
    },
    "ops-standard": {
        "source": "재배치 운영 기준 (팀 작성 v1)",
        "text": (
            "빈 대여소 해소를 과적재 해소보다 우선한다. 진입 불가 대여소는 해당 근무조 경로에서 제외하고 "
            "인수인계 메모에 남긴다. 현장 실제 재고가 앱과 다르면 현장 값을 기준으로 다시 계산한다."
        ),
    },
    "handover-template": {
        "source": "근무조 인수인계 요약 양식",
        "text": "인수인계 요약에는 포기 거점과 사유, 진입 불가 대여소, 미처리 변경안을 적는다.",
    },
}

NEARBY = {"ST-9999": ["ST-2003", "ST-2005"]}


def report(report_id: str, truck_id: str, station_id: str, actual: int, issue: str | None = None, memo: str | None = None) -> dict:
    """internal-api FieldReport as the backend stores it (`driver` is already masked by the backend)."""
    return {
        "reportId": report_id, "truckId": truck_id, "driver": f"기사 {truck_id[-1]}", "stationId": station_id,
        "actualBikes": actual, "issue": issue, "memo": memo, "reportedAt": "2026-10-24T13:05:00+09:00",
    }


@dataclass
class World:
    """Backend state for one scenario plus a log of every tool execution."""

    stations: dict[str, dict[str, Any]] = field(default_factory=lambda: copy.deepcopy(STATIONS))
    trucks: dict[str, dict[str, Any]] = field(default_factory=lambda: copy.deepcopy(TRUCKS))
    reports: list[dict[str, Any]] = field(default_factory=list)
    big_risk_list: int = 0  # >0: get_risk_stations pads the result with this many rows (truncation scenario)
    tool_delay_s: float = 0.0  # request_route_plan latency (timeout scenario)
    force_grade: str | None = None  # stands in for the backend grade rule (e.g. MAJOR from a long detour)
    log: list[dict[str, Any]] = field(default_factory=list)
    constraints: list[dict[str, Any]] = field(default_factory=list)
    approved: set[str] = field(default_factory=set)  # approval ids the backend recorded as APPROVED
    candidates: dict[str, dict[str, Any]] = field(default_factory=dict)
    active: dict[str, Any] = field(default_factory=dict)
    _seq: itertools.count = field(default_factory=itertools.count)
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1))

    def __post_init__(self) -> None:
        # The active plan was made before this scenario's field reports arrived.
        reports, self.reports = self.reports, []
        plan = self._plan("PLAN", list(self.trucks), set(), base_routes={})
        self.reports = reports
        self.active = {**plan, "planId": "P-0", "version": 0, "status": "ACTIVE"}
        self._ids = itertools.count(1)

    # --- bookkeeping -----------------------------------------------------------------------------------------
    def record(self, tool: str, args: dict[str, Any], destructive: bool = False) -> None:
        self.log.append({"seq": next(self._seq), "tool": tool, "args": args, "destructive": destructive})

    def next_seq(self) -> int:
        return next(self._seq)

    @property
    def executed_changes(self) -> list[dict[str, Any]]:
        return [entry for entry in self.log if entry["destructive"]]

    def require_approval(self, approval_id: str) -> None:
        if approval_id not in self.approved:
            raise ToolError(f"approvalId {approval_id}는 승인되지 않았다 (APPROVAL_REQUIRED). 승인 후 다시 호출한다.")

    def station(self, station_id: str) -> dict[str, Any]:
        if station_id not in self.stations:
            near = NEARBY.get(station_id, ["ST-2003", "ST-2005"])
            raise ToolError(f"'{station_id}' 대여소가 없다. 권역 안의 가까운 대여소: {', '.join(near)}")
        return self.stations[station_id]

    # --- planning (stand-in for backend + OR-Tools) ------------------------------------------------------------
    def inventory(self) -> dict[str, int]:
        bikes = {sid: s["bikes"] for sid, s in self.stations.items()}
        for r in self.reports:
            bikes[r["stationId"]] = r["actualBikes"]
        for c in self.constraints:
            if c["type"] == "INVENTORY_OVERRIDE":
                bikes[c["stationIds"][0]] = c["value"]
        return bikes

    def _blocked(self, exclude: set[str]) -> set[str]:
        out = set(exclude)
        for c in self.constraints:
            if c["type"] in {"STATION_SUSPENDED", "STATION_BLOCKED"}:
                out.update(c["stationIds"])
        for r in self.reports:
            if r["issue"] in {"BLOCKED", "CONSTRUCTION"}:
                out.add(r["stationId"])
        return out

    def _plan(self, kind: str, trucks: list[str], exclude: set[str], base_routes: dict[str, list]) -> dict[str, Any]:
        bikes, blocked = self.inventory(), self._blocked(exclude)
        taken = {s["stationId"] for stops in base_routes.values() for s in stops}
        needs = []
        for sid, s in self.stations.items():
            if s["risk"] == "NONE" or sid in blocked or sid in taken:
                continue
            delta = bikes[sid] - s["docks"] // 2
            if delta:
                needs.append((sid, delta, abs(delta) * (2 if delta < 0 else 1)))
        needs.sort(key=lambda n: -n[2])
        routes = {tid: list(stops) for tid, stops in base_routes.items()}
        slots = {tid: 2 for tid in trucks}
        unserved = []
        for sid, delta, priority in needs:
            truck = next((tid for tid in trucks if slots[tid] > 0), None)
            if truck is None:
                unserved.append({"stationId": sid, "stationName": self.stations[sid]["name"], "priority": priority,
                                 "reason": "트럭 시간·적재량 한계"})
                continue
            slots[truck] -= 1
            risk = RISK_KO[self.stations[sid]["risk"]]
            routes.setdefault(truck, []).append({
                "seq": len(routes.get(truck, [])) + 1, "stationId": sid, "stationName": self.stations[sid]["name"],
                "action": "PICKUP" if delta > 0 else "DROPOFF", "bikes": abs(delta), "eta": None,
                "reason": f"{risk} 위험, 필요 {abs(delta)}대", "status": "PENDING",
            })
        n = next(self._ids)
        return {
            "planId": f"P-{n}", "shiftId": SHIFT_ID, "version": n, "status": "DRAFT", "provisional": False,
            "routes": [
                {"truckId": tid, "capacity": self.trucks[tid]["capacity"], "startLoad": 0, "stops": stops}
                for tid, stops in routes.items()
            ],
            "unserved": unserved, "explanation": None,
        }

    def candidate(self, kind: str, truck_ids: list[str], exclude: list[str]) -> dict[str, Any]:
        active = {r["truckId"]: r["stops"] for r in self.active["routes"]}
        if kind == "PLAN":
            plan, grade, diffs = self._plan("PLAN", list(self.trucks), set(exclude), {}), None, []
        else:
            redo = truck_ids or list(self.trucks)
            keep = {tid: stops for tid, stops in active.items() if tid not in redo}
            plan = self._plan("REPLAN", redo, set(exclude), keep)
            after = {r["truckId"]: r["stops"] for r in plan["routes"]}
            diffs = []
            for tid in redo:
                before_ids = [s["stationId"] for s in active.get(tid, [])]
                after_ids = [s["stationId"] for s in after.get(tid, [])]
                if before_ids != after_ids or [s["bikes"] for s in active.get(tid, [])] != [s["bikes"] for s in after.get(tid, [])]:
                    diffs.append({"truckId": tid, "before": active.get(tid, []), "after": after.get(tid, []),
                                  "addedMinutes": 3 * max(0, len(after_ids) - len(before_ids))})
            removed = {s for d in diffs for s in {x["stationId"] for x in d["before"]} - {x["stationId"] for x in d["after"]}}
            if self.force_grade:
                grade = self.force_grade
            elif not diffs:
                grade = "NONE"
            else:
                grade = "MAJOR" if len(redo) > 1 or len(removed) > 2 else "MINOR"
        cid = f"C-{plan['version']}"
        self.candidates[cid] = {"candidateId": cid, "status": "OK", "plan": plan, "grade": grade, "diffs": diffs}
        return self.candidates[cid]


def business_server(world: World) -> FastMCP:
    mcp = FastMCP("business")

    def dump(obj: Any) -> str:
        return json.dumps(obj, ensure_ascii=False)

    @mcp.tool(annotations=READ)
    def get_risk_stations(region_id: str, horizon_h: int = 3, limit: int = 50) -> str:
        """빈·과적재 위험 대여소 (우선순위 내림차순). 반환: stationId, name, docks(거치대 수), bikes(현재 재고),
        forecastBikes(horizon_h 뒤 예상 재고), risk(EMPTY|OVERFLOW), neededBikes(양수 회수, 음수 배치), priority, reasons."""
        world.record("get_risk_stations", {"region_id": region_id, "horizon_h": horizon_h, "limit": limit})
        bikes = world.inventory()
        rows = []
        for sid, s in world.stations.items():
            if s["risk"] == "NONE":
                continue
            needed = bikes[sid] - s["docks"] // 2
            rows.append({**s, "bikes": bikes[sid], "neededBikes": needed, "priority": abs(needed) * (2 if needed < 0 else 1),
                         "emptyAt": None, "reasons": [f"{RISK_KO[s['risk']]} 위험"]})
        rows.sort(key=lambda r: -r["priority"])
        rows += [
            {**STATIONS["ST-2008"], "stationId": f"ST-9{i:03d}", "name": f"패딩 대여소 {i}", "neededBikes": 0, "priority": 0,
             "emptyAt": None, "reasons": []}
            for i in range(world.big_risk_list)
        ]
        return dump(rows if world.big_risk_list else rows[:limit])

    @mcp.tool(annotations=READ)
    def get_station_detail(station_id: str) -> str:
        """대여소 상세. 반환: StationStatus + district, recentReports(현장 보고)."""
        world.record("get_station_detail", {"station_id": station_id})
        s = world.station(station_id)
        recent = [r for r in world.reports if r["stationId"] == station_id]
        return dump({**s, "bikes": world.inventory()[station_id], "district": "영등포구", "recentReports": recent})

    @mcp.tool(annotations=READ)
    def get_forecast(station_ids: list[str], start: str, horizon_h: int = 3) -> str:
        """대여소별 예측. 반환: stationId, start, horizonH, rentals(대여 예상), returns(반납 예상), modelVersion."""
        world.record("get_forecast", {"station_ids": station_ids, "start": start, "horizon_h": horizon_h})
        out = []
        for sid in station_ids:
            s = world.station(sid)
            change = s["forecastBikes"] - s["bikes"]
            out.append({"stationId": sid, "start": start, "horizonH": horizon_h, "rentals": max(0, -change),
                        "returns": max(0, change), "modelVersion": "baseline-avg"})
        return dump(out)

    @mcp.tool(annotations=READ)
    def get_current_plan(shift_id: str) -> str:
        """현재 활성 계획. 반환: planId, version, status, routes[truckId, stops[seq, stationId, action, bikes, reason]],
        unserved[stationId, priority, reason]."""
        world.record("get_current_plan", {"shift_id": shift_id})
        return dump(world.active)

    @mcp.tool(annotations=READ)
    def get_truck_status(shift_id: str) -> str:
        """트럭 상태. 반환: truckId, capacity(대), load(대), lat, lon, shiftEnd, remainingStops."""
        world.record("get_truck_status", {"shift_id": shift_id})
        routes = {r["truckId"]: r["stops"] for r in world.active["routes"]}
        return dump([{**t, "remainingStops": routes.get(tid, [])} for tid, t in world.trucks.items()])

    @mcp.tool(annotations=READ)
    def get_field_reports(shift_id: str, since: str | None = None) -> str:
        """근무조 현장 보고. 반환: reportId, truckId, driver(마스킹), stationId, actualBikes, issue, memo, reportedAt."""
        world.record("get_field_reports", {"shift_id": shift_id, "since": since})
        return dump(world.reports)

    @mcp.tool(annotations=COMPUTE)
    async def request_route_plan(
        shift_id: str, kind: str, truck_ids: list[str] | None = None, exclude_station_ids: list[str] | None = None
    ) -> str:
        """경로 계산 (활성화 전 후보). kind: PLAN(근무조 계획) | REPLAN(재경로, truck_ids만 다시 계산).
        현장 보고의 실제 재고와 진입 불가는 자동 반영된다. 반환: candidateId, status, plan, grade(NONE|MINOR|MAJOR, REPLAN만), diffs."""
        args = {"shift_id": shift_id, "kind": kind, "truck_ids": truck_ids or [], "exclude_station_ids": exclude_station_ids or []}
        world.record("request_route_plan", args)
        for sid in args["exclude_station_ids"]:
            world.station(sid)
        if world.tool_delay_s:
            await asyncio.sleep(world.tool_delay_s)
        return dump(world.candidate(kind, args["truck_ids"], args["exclude_station_ids"]))

    @mcp.tool(annotations=CHANGE)
    def add_constraint(
        shift_id: str, type: str, station_ids: list[str], valid_from: str, valid_to: str, reason: str,
        approval_id: str, task_id: str, value: int | None = None,
    ) -> str:
        """제약 추가 (관리자 승인 필요). type: TARGET_ADJUST | STATION_SUSPENDED | STATION_BLOCKED | INVENTORY_OVERRIDE | PRIORITY_BOOST."""
        for sid in station_ids:
            world.station(sid)
        world.require_approval(approval_id)
        args = {"shift_id": shift_id, "type": type, "station_ids": station_ids, "valid_from": valid_from,
                "valid_to": valid_to, "reason": reason, "value": value, "approval_id": approval_id, "task_id": task_id}
        world.record("add_constraint", args, True)
        constraint = {"constraintId": f"CN-{len(world.constraints) + 1}", "shiftId": shift_id, "type": type,
                      "stationIds": station_ids, "value": value, "validFrom": valid_from, "validTo": valid_to,
                      "reason": reason, "approvalId": approval_id, "taskId": task_id}
        world.constraints.append(constraint)
        return dump(constraint)

    @mcp.tool(annotations=CHANGE)
    def submit_plan(plan_id: str, approval_id: str, task_id: str) -> str:
        """계획 활성화 (관리자 승인 필요). 반환: 활성화된 Plan."""
        world.require_approval(approval_id)
        plan = next((c["plan"] for c in world.candidates.values() if c["plan"]["planId"] == plan_id), None)
        if plan is None:
            raise ToolError(f"계획 {plan_id}가 없거나 더 새로운 후보로 대체됐다 (409).")
        world.record("submit_plan", {"plan_id": plan_id, "approval_id": approval_id, "task_id": task_id}, True)
        world.active = {**plan, "status": "ACTIVE"}
        return dump(world.active)

    @mcp.tool(annotations=CHANGE)
    def propose_route_change(truck_id: str, candidate_id: str, approval_id: str, task_id: str) -> str:
        """트럭 경로 변경 적용 (작은 변경은 기사, 큰 변경은 관리자 승인 필요). 반환: 적용된 Route."""
        world.require_approval(approval_id)
        candidate = world.candidates.get(candidate_id)
        route = next((r for r in candidate["plan"]["routes"] if r["truckId"] == truck_id), None) if candidate else None
        if route is None:
            raise ToolError(f"후보 {candidate_id}에 {truck_id} 경로가 없다 (409).")
        world.record("propose_route_change", {"truck_id": truck_id, "candidate_id": candidate_id,
                                              "approval_id": approval_id, "task_id": task_id}, True)
        world.active = {**world.active, "routes": [route if r["truckId"] == truck_id else r for r in world.active["routes"]]}
        return dump(route)

    return mcp


def rag_server(world: World) -> FastMCP:
    mcp = FastMCP("rag")

    @mcp.tool(annotations=READ)
    def search_docs(query: str, top_k: int = 5) -> str:
        """근거 문서 검색. 반환: chunk 목록 [doc_id(doc://...), source, text]."""
        world.record("search_docs", {"query": query, "top_k": top_k})
        words = [w for w in query.replace(",", " ").split() if len(w) > 1]
        scored = sorted(
            ((sum(w in d["text"] or w in d["source"] for w in words), doc_id, d) for doc_id, d in DOCS.items()),
            key=lambda x: -x[0],
        )
        hits = [{"doc_id": f"doc://{doc_id}", "source": d["source"], "text": d["text"]} for _, doc_id, d in scored[:top_k]]
        return json.dumps(hits, ensure_ascii=False)

    @mcp.resource("doc://{doc_id}")
    def doc(doc_id: str) -> str:
        return DOCS[doc_id]["text"]

    return mcp


@asynccontextmanager
async def connect_world(world: World, trace_header: str = "X-Trace-Id") -> AsyncIterator[list[BaseTool]]:
    """MCP client sessions to both fake servers; tools are prefixed `business_` / `rag_` like production."""
    async with AsyncExitStack() as stack:
        tools: list[BaseTool] = []
        for name, server in (("business", business_server(world)), ("rag", rag_server(world))):
            session = await stack.enter_async_context(create_connected_server_and_client_session(server))
            tools += await load_mcp_tools(
                session,
                server_name=name,
                tool_name_prefix=True,
                tool_interceptors=[TraceHeaderInterceptor(trace_header)],
            )
        yield tools
