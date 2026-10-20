# Author: Cheon Seonghun
# Date: 20OCT2026
# Purpose: In-memory business-mcp / rag-mcp doubles with the 05 view tool contract, for tests and scenarios.

"""Fake MCP servers over the real MCP protocol (in-memory transport).

Tool names, arguments, and annotations follow docs/images/views/05-mcp-connection.png. This is a test double for
evaluation only; the real servers belong to the MCP · RAG role.
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

STATIONS: dict[str, dict[str, Any]] = {
    "ST-2001": {"name": "여의나루역 1번출구", "capacity": 20, "bikes": 18, "predicted_3h": 24, "risk": "full"},
    "ST-2002": {"name": "여의나루역 2번출구", "capacity": 15, "bikes": 14, "predicted_3h": 19, "risk": "full"},
    "ST-2003": {"name": "여의도공원 앞", "capacity": 20, "bikes": 8, "predicted_3h": 1, "risk": "empty"},
    "ST-2004": {"name": "국회의사당역 6번출구", "capacity": 15, "bikes": 1, "predicted_3h": 0, "risk": "empty"},
    "ST-2005": {"name": "여의도역 5번출구", "capacity": 25, "bikes": 2, "predicted_3h": 0, "risk": "empty"},
    "ST-2006": {"name": "IFC몰 앞", "capacity": 20, "bikes": 19, "predicted_3h": 23, "risk": "full"},
    "ST-2007": {"name": "샛강역 1번출구", "capacity": 15, "bikes": 0, "predicted_3h": 0, "risk": "empty"},
    "ST-2008": {"name": "63빌딩 앞", "capacity": 15, "bikes": 9, "predicted_3h": 8, "risk": "normal"},
}

TRUCKS: dict[str, dict[str, Any]] = {
    "TR-1": {
        "capacity": 15,
        "load": 0,
        "at_station": "ST-2008",
        "driver_name": "김민수",
        "driver_phone": "010-2345-6789",
        "truck_position": {"lat": 37.5194, "lon": 126.9403},
    },
    "TR-2": {
        "capacity": 15,
        "load": 0,
        "at_station": "ST-2006",
        "driver_name": "박지훈",
        "driver_phone": "010-9876-5432",
        "truck_position": {"lat": 37.5251, "lon": 126.9255},
    },
}

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

AUDIT: list[dict[str, Any]] = [
    {"at": "12:31", "event": "plan_approved", "plan_id": "P-0", "by": "manager",
     "detail": "만차 위험 ST-2001, ST-2002, ST-2006 회수 우선 (pickup 3곳, dropoff 2곳)"},
    {"at": "13:05", "event": "field_report", "truck_id": "TR-2", "station_id": "ST-2006",
     "detail": "실제 재고 23대 (앱 19대)"},
    {"at": "13:07", "event": "proposal_accepted", "proposal_id": "PR-0", "by": "driver",
     "detail": "TR-2 ST-2006 회수 대수 증가"},
]


@dataclass
class World:
    """Mutable business state for one scenario plus a log of every tool execution."""

    stations: dict[str, dict[str, Any]] = field(default_factory=lambda: copy.deepcopy(STATIONS))
    trucks: dict[str, dict[str, Any]] = field(default_factory=lambda: copy.deepcopy(TRUCKS))
    big_risk_list: int = 0  # >0: get_risk_stations pads the result with this many extra rows (truncation scenario)
    tool_delay_s: float = 0.0  # request_route_plan latency (timeout scenario)
    log: list[dict[str, Any]] = field(default_factory=list)
    constraints: list[dict[str, Any]] = field(default_factory=list)
    _seq: itertools.count = field(default_factory=itertools.count)
    _plans: itertools.count = field(default_factory=lambda: itertools.count(1))

    def record(self, tool: str, args: dict[str, Any], destructive: bool = False) -> None:
        self.log.append({"seq": next(self._seq), "tool": tool, "args": args, "destructive": destructive})

    def next_seq(self) -> int:
        return next(self._seq)

    @property
    def executed_changes(self) -> list[dict[str, Any]]:
        return [entry for entry in self.log if entry["destructive"]]

    def station(self, station_id: str) -> dict[str, Any]:
        if station_id not in self.stations:
            near = NEARBY.get(station_id, ["ST-2003", "ST-2005"])
            raise ToolError(f"대여소 '{station_id}' 없음. 권역 내 가까운 대여소: {', '.join(near)}")
        return {"station_id": station_id, **self.stations[station_id]}

    def plan(self, constraints: list[dict[str, Any]]) -> dict[str, Any]:
        """Deterministic stand-in for OR-Tools: serve the most urgent risky stations within truck capacity."""
        blocked = {c.get("station_id") for c in constraints if c.get("type") == "blocked"}
        for c in constraints:
            blocked.update(c.get("station_ids", []) if c.get("type") in {"suspended", "excluded"} else [])
        bikes = {sid: s["bikes"] for sid, s in self.stations.items()}
        for c in constraints:
            if c.get("type") == "actual_bikes" and c.get("station_id") in bikes:
                bikes[c["station_id"]] = int(c["bikes"])
        need = []
        for sid, s in self.stations.items():
            if s["risk"] == "normal" or sid in blocked:
                continue
            target = s["capacity"] // 2
            delta = bikes[sid] - target
            need.append((sid, delta, abs(delta) * (2 if delta < 0 else 1)))
        need.sort(key=lambda n: -n[2])
        routes, unserved = [], []
        slots = {tid: 2 for tid in self.trucks}  # 정류장 수 한계 (시간 한계 대용)
        for sid, delta, priority in need:
            truck = next((tid for tid, left in slots.items() if left > 0), None)
            if truck is None:
                unserved.append({"station_id": sid, "priority": priority, "reason": "트럭 시간·적재량 한계"})
                continue
            slots[truck] -= 1
            action = "pickup" if delta > 0 else "dropoff"
            routes.append({"truck_id": truck, "station_id": sid, "action": action, "bikes": abs(delta)})
        plan_id = f"P-{next(self._plans)}"
        by_truck: dict[str, list[dict[str, Any]]] = {}
        for stop in routes:
            by_truck.setdefault(stop.pop("truck_id"), []).append(stop)
        return {
            "plan_id": plan_id,
            "routes": [{"truck_id": tid, "stops": stops} for tid, stops in by_truck.items()],
            "unserved": unserved,
            "excluded": sorted(s for s in blocked if s),
        }


def business_server(world: World) -> FastMCP:
    mcp = FastMCP("business")

    def dump(obj: Any) -> str:
        return json.dumps(obj, ensure_ascii=False)

    @mcp.tool(annotations=READ)
    def get_risk_stations(region: str, horizon_h: int = 3) -> str:
        """권역의 빈·만차 위험 대여소. 반환: station_id, name, bikes(대), predicted_3h(대), risk(empty|full)."""
        world.record("get_risk_stations", {"region": region, "horizon_h": horizon_h})
        rows = [{"station_id": sid, **s} for sid, s in world.stations.items() if s["risk"] != "normal"]
        rows += [
            {"station_id": f"ST-9{i:03d}", "name": f"패딩 대여소 {i}", "bikes": 5, "predicted_3h": 5, "risk": "normal"}
            for i in range(world.big_risk_list)
        ]
        return dump(rows)

    @mcp.tool(annotations=READ)
    def get_station_detail(station_id: str) -> str:
        """대여소 상세. 반환: station_id, name, capacity(거치대 수), bikes(대), predicted_3h(대), risk."""
        world.record("get_station_detail", {"station_id": station_id})
        return dump(world.station(station_id))

    @mcp.tool(annotations=READ)
    def get_forecast(station_ids: list[str], start: str, horizon_h: int = 3) -> str:
        """대여소별 예측 재고. 반환: station_id, predicted(대)."""
        world.record("get_forecast", {"station_ids": station_ids, "start": start, "horizon_h": horizon_h})
        return dump([{"station_id": sid, "predicted": world.station(sid)["predicted_3h"]} for sid in station_ids])

    @mcp.tool(annotations=READ)
    def get_current_plan(shift_id: str) -> str:
        """현재 활성 계획. 반환: plan_id, routes[truck_id, stops[station_id, action, bikes]]."""
        world.record("get_current_plan", {"shift_id": shift_id})
        return dump({"shift_id": shift_id, **world.plan(world.constraints)})

    @mcp.tool(annotations=READ)
    def get_truck_status(shift_id: str) -> str:
        """트럭 상태. 반환: truck_id, capacity(대), load(대), at_station, 기사 정보."""
        world.record("get_truck_status", {"shift_id": shift_id})
        return dump([{"truck_id": tid, **t} for tid, t in world.trucks.items()])

    @mcp.tool(annotations=READ)
    def get_field_reports(shift_id: str) -> str:
        """근무조 현장 보고 목록."""
        world.record("get_field_reports", {"shift_id": shift_id})
        return dump([])

    @mcp.tool(annotations=READ)
    def get_audit(shift_id: str) -> str:
        """근무조 감사 기록 (계획 승인, 현장 보고, 변경 수락·거절). 반환: at, event, 대상 ID, by, detail."""
        world.record("get_audit", {"shift_id": shift_id})
        return dump(AUDIT + [{"event": e["tool"], "args": e["args"]} for e in world.executed_changes])

    @mcp.tool(annotations=COMPUTE)
    async def request_route_plan(shift_id: str, constraints: list[dict[str, Any]] | None = None) -> str:
        """제약 조건을 반영해 경로를 계산한다. constraints 예: {"type":"blocked","station_id":"ST-2005"},
        {"type":"actual_bikes","station_id":"ST-2003","bikes":3}, {"type":"suspended","station_ids":[...]},
        {"type":"excluded","station_ids":[...]}.
        반환: plan_id, routes, unserved[station_id, priority, reason], excluded."""
        constraints = constraints or []
        world.record("request_route_plan", {"shift_id": shift_id, "constraints": constraints})
        if world.tool_delay_s:
            await asyncio.sleep(world.tool_delay_s)
        return dump(world.plan([*world.constraints, *constraints]))

    @mcp.tool(annotations=CHANGE)
    def add_constraint(type: str, station_ids: list[str], period: str, reason: str) -> str:
        """제약 조건 추가 (관리자 승인 필요). type: suspended|blocked. period 예: 2026-10-24 17:00~22:00."""
        for sid in station_ids:
            world.station(sid)
        world.record("add_constraint", {"type": type, "station_ids": station_ids, "period": period, "reason": reason}, True)
        world.constraints.append({"type": type, "station_ids": station_ids})
        return dump({"constraint_id": f"C-{len(world.constraints)}", "status": "active"})

    @mcp.tool(annotations=CHANGE)
    def submit_plan(plan_id: str) -> str:
        """계획 활성화 (관리자 승인 필요)."""
        world.record("submit_plan", {"plan_id": plan_id}, True)
        return dump({"plan_id": plan_id, "status": "active"})

    @mcp.tool(annotations=CHANGE)
    def propose_route_change(truck_id: str, route: list[str]) -> str:
        """트럭 경로 변경 제안 (승인 필요). route: 남은 정류장 ID 순서."""
        for sid in route:
            world.station(sid)
        world.record("propose_route_change", {"truck_id": truck_id, "route": route}, True)
        return dump({"proposal_id": f"PR-{len(world.executed_changes)}", "status": "pending_driver"})

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
