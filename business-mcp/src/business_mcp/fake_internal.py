# Author: 정유은
# Date: 20OCT2026
# Purpose: M1용 가짜 /internal/** 구현. 백엔드 전에 에이전트·MCP를 함께 개발·시험하기 위한 대역.

"""M1용 가짜 /internal/** 구현 (docs/specs/internal-api.yaml 형식).

백엔드가 준비되기 전에 에이전트와 MCP를 함께 개발하기 위한 대역이다.
**대여소 이름·좌표·재고는 모두 가짜 값**이며 실제 운영 근거로 쓰지 않는다.
업무 규칙(위험 판정, 변경 등급, 승인 확인, 버전 충돌)은 백엔드의 몫이라 여기서는 흉내만 낸다.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Any

from .errors import InternalApiError

Json = dict[str, Any]

FIXED_NOW = datetime.fromisoformat("2026-10-21T08:05:00+09:00")
REGION_ID = "gwangjin-seongdong"
SHIFT_ID = f"{REGION_ID}:2026-10-21:AM"
SHIFT_END = "2026-10-21T10:00:00+09:00"
TRUCK_CAPACITY = 15
FORECAST_MODEL = "baseline-avg-2026-10-21"

# netPerH: 시간당 순유입(반납 - 대여) 가짜 값. 음수면 비어 가는 대여소.
STATIONS: dict[str, Json] = {
    "ST-1023": {"name": "뚝섬유원지역 1번출구", "district": "광진구", "lat": 37.5316, "lon": 127.0669,
                "docks": 20, "bikes": 1, "role": "FIXED_DROPOFF", "elevationM": 18, "netPerH": -3},
    "ST-1031": {"name": "뚝섬유원지역 3번출구", "district": "광진구", "lat": 37.5307, "lon": 127.0661,
                "docks": 15, "bikes": 4, "role": "VARIABLE", "elevationM": 17, "netPerH": -1},
    "ST-1050": {"name": "구의역 1번출구", "district": "광진구", "lat": 37.5370, "lon": 127.0857,
                "docks": 15, "bikes": 6, "role": "VARIABLE", "elevationM": 24, "netPerH": -2},
    "ST-1077": {"name": "군자역 7번출구", "district": "광진구", "lat": 37.5573, "lon": 127.0794,
                "docks": 12, "bikes": 2, "role": "FIXED_DROPOFF", "elevationM": 31, "netPerH": -2},
    "ST-1188": {"name": "자양사거리", "district": "광진구", "lat": 37.5349, "lon": 127.0820,
                "docks": 10, "bikes": 0, "role": "FIXED_DROPOFF", "elevationM": 22, "netPerH": -1},
    "ST-2011": {"name": "건대입구역 사거리", "district": "광진구", "lat": 37.5404, "lon": 127.0700,
                "docks": 25, "bikes": 38, "role": "FIXED_PICKUP", "elevationM": 20, "netPerH": 4},
    "ST-2240": {"name": "어린이대공원역 3번출구", "district": "광진구", "lat": 37.5481, "lon": 127.0745,
                "docks": 15, "bikes": 29, "role": "FIXED_PICKUP", "elevationM": 27, "netPerH": 2},
    "ST-2302": {"name": "성수역 3번출구", "district": "성동구", "lat": 37.5446, "lon": 127.0560,
                "docks": 10, "bikes": 17, "role": "FIXED_PICKUP", "elevationM": 19, "netPerH": 1},
}


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _stop(seq: int, sid: str, action: str, bikes: int, load_after: int, eta: str, reason: str, status: str) -> Json:
    return {"seq": seq, "stationId": sid, "stationName": STATIONS[sid]["name"], "action": action, "bikes": bikes,
            "loadAfter": load_after, "eta": eta, "reason": reason, "status": status}


def _plan_id(version: int) -> str:
    return f"PL-{SHIFT_ID}-v{version}"


def _initial_plan() -> Json:
    t = "2026-10-21T{}:00+09:00".format
    return {
        "planId": _plan_id(3), "shiftId": SHIFT_ID, "version": 3, "status": "ACTIVE", "provisional": False,
        "routes": [
            {"truckId": "TR-01", "capacity": TRUCK_CAPACITY, "startLoad": 0, "stops": [
                _stop(1, "ST-2011", "PICKUP", 12, 12, t("07:12"), "과적재 예상 +12대", "DONE"),
                _stop(2, "ST-1023", "DROPOFF", 8, 4, t("07:40"), "07:40 빈 대여소 예상", "DONE"),
                _stop(3, "ST-1031", "DROPOFF", 4, 0, t("07:55"), "빈 대여소 위험", "DONE"),
                _stop(4, "ST-2240", "PICKUP", 10, 10, t("08:20"), "과적재 예상 +10대", "PENDING"),
                _stop(5, "ST-1077", "DROPOFF", 10, 0, t("08:45"), "출근 시간 빈 대여소 위험", "PENDING"),
            ]},
            {"truckId": "TR-02", "capacity": TRUCK_CAPACITY, "startLoad": 0, "stops": [
                _stop(1, "ST-2302", "PICKUP", 12, 12, t("07:20"), "과적재 예상 +7대", "DONE"),
                _stop(2, "ST-1050", "DROPOFF", 6, 6, t("08:10"), "빈 대여소 위험", "PENDING"),
            ]},
            {"truckId": "TR-03", "capacity": TRUCK_CAPACITY, "startLoad": 0, "stops": []},
        ],
        "unserved": [{"stationId": "ST-1188", "stationName": STATIONS["ST-1188"]["name"], "priority": 21.0,
                      "reason": "TIME"}],
        "explanation": "광진구 북부의 출근 시간 빈 대여소 위험을 우선 해소했습니다. (가짜 데이터)",
    }


TRUCK_POSITIONS = {"TR-01": (37.5395, 127.0712, 0), "TR-02": (37.5370, 127.0857, 12), "TR-03": (37.5420, 127.0790, 0)}


def _initial_reports() -> list[Json]:
    return [
        {"reportId": "FR-883", "truckId": "TR-01", "driver": "TR-01 기사", "stationId": "ST-1031", "actualBikes": 1,
         "issue": None, "memo": None, "reportedAt": "2026-10-21T07:57:00+09:00"},
        {"reportId": "FR-881", "truckId": "TR-01", "driver": "TR-01 기사", "stationId": "ST-1023", "actualBikes": 0,
         "issue": "INVENTORY_MISMATCH", "memo": "앱 3대, 실제 0대", "reportedAt": "2026-10-21T07:41:00+09:00"},
        {"reportId": "FR-880", "truckId": "TR-02", "driver": "TR-02 기사", "stationId": "ST-2302", "actualBikes": 17,
         "issue": None, "memo": None, "reportedAt": "2026-10-21T07:22:00+09:00"},
    ]


def _initial_audit() -> list[Json]:
    return [
        {"auditId": "AU-5501", "occurredAt": "2026-10-21T06:52:00+09:00", "type": "PLAN_ACTIVATED",
         "actor": {"role": "MANAGER", "display": "관리자"}, "targetId": _plan_id(3),
         "detail": "오전 계획 v3 승인. 회수 3곳, 배치 4곳, 포기 1곳(ST-1188, 시간 한계)"},
        {"auditId": "AU-5502", "occurredAt": "2026-10-21T07:41:00+09:00", "type": "FIELD_REPORT",
         "actor": {"role": "DRIVER", "display": "TR-01 기사"}, "targetId": "FR-881",
         "detail": "ST-1023 실제 재고 0대 (앱 3대)"},
    ]


def _problem(status: int, code: str, detail: str, suggestions: list[str] | None = None) -> InternalApiError:
    body: Json = {"type": "about:blank", "title": code, "status": status, "code": code, "detail": detail}
    if suggestions:
        body["suggestions"] = suggestions
    return InternalApiError.from_problem(status, body)


class FakeInternalApi:
    """InternalApi 계약을 메모리 상태로 흉내 낸다.

    승인: 변경 API는 approve()로 승인해 둔 approvalId만 통과시킨다 (그 밖은 403).
    제약·계획은 관리자(MANAGER) 승인 한 장, 경로 변경은 후보의 requiredApprovers 순서가 모두 승인돼야 한다
    (MINOR [DRIVER], MAJOR [MANAGER, DRIVER]). 경로 변경에 넘기는 approvalId는 마지막 카드인 기사 수락이다.
    """

    def __init__(self, *, now: datetime = FIXED_NOW) -> None:
        self._now = now
        self._plans: dict[int, Json] = {3: _initial_plan()}
        self._latest = 3
        self._reports = _initial_reports()
        self._audit = _initial_audit()
        self._constraints: list[Json] = []
        self._candidates: dict[str, Json] = {}
        self._latest_candidate: str | None = None
        self._approved: dict[str, Json] = {}  # approvalId -> {role, taskId, candidateId}
        self._used_approvals: dict[str, Json] = {}
        self._seq = {"constraint": 300, "candidate": 0}

    # ── 시험 도우미 (백엔드의 승인 API 대역) ─────────────────
    def approve(self, approval_id: str, *, role: str = "MANAGER", task_id: str | None = None,
                candidate_id: str | None = None) -> None:
        """사람이 카드를 승인한 기록. task_id가 None이면 어느 작업에서든 통한다 (시험 편의)."""
        self._approved[approval_id] = {"role": role, "taskId": task_id, "candidateId": candidate_id}
        # backend는 승인 결정도 감사 기록에 남긴다 (internal-api 0.5.0 AuditEvent APPROVAL_APPROVED).
        self._audit.append({"auditId": f"AU-{5502 + len(self._audit)}", "occurredAt": _iso(self._now),
                            "type": "APPROVAL_APPROVED",
                            "actor": {"role": role, "display": "관리자" if role == "MANAGER" else "기사"},
                            "targetId": approval_id, "detail": f"{role} 승인"})

    # ── 공통 ─────────────────────────────────────────────
    def _next(self, key: str) -> int:
        self._seq[key] += 1
        return self._seq[key]

    def _station(self, station_id: str) -> Json:
        if station_id not in STATIONS:
            digits = "".join(ch for ch in station_id if ch.isdigit())
            target = int(digits) if digits else 0
            near = sorted(STATIONS, key=lambda sid: abs(int(sid[3:]) - target))[:3]
            raise _problem(404, "STATION_NOT_FOUND",
                           f"'{station_id}' 대여소가 없습니다. 권역 안의 비슷한 대여소: {', '.join(near)}", near)
        return STATIONS[station_id]

    def _shift(self, shift_id: str) -> None:
        if shift_id != SHIFT_ID:
            raise _problem(404, "SHIFT_NOT_FOUND", f"'{shift_id}' 근무조가 없습니다. 오늘 근무조: {SHIFT_ID}", [SHIFT_ID])

    def _status(self, sid: str, horizon_h: int = 3) -> Json:
        s = STATIONS[sid]
        forecast = s["bikes"] + s["netPerH"] * horizon_h
        risk = "EMPTY" if forecast < 2 else "OVERFLOW" if forecast > s["docks"] else "NONE"
        return {"stationId": sid, "name": s["name"], "lat": s["lat"], "lon": s["lon"], "docks": s["docks"],
                "bikes": s["bikes"], "observedAt": _iso(self._now - timedelta(minutes=5)),
                "forecastBikes": forecast, "risk": risk, "role": s["role"], "elevationM": s["elevationM"]}

    def _approval(self, approval_id: str, task_id: str, role: str) -> Json | None:
        """승인 기록을 찾는다. 없거나, 다른 작업이거나, 승인 주체가 다르면 None."""
        rec = self._approved.get(approval_id)
        if rec is None or rec["role"] != role or rec["taskId"] not in (None, task_id):
            return None
        return rec

    def _check_approval(self, request: Json, op: Json, role: str) -> Json | None:
        """승인되지 않았으면 403. 같은 approvalId로 같은 작업을 다시 부르면 이전 결과를 준다."""
        approval_id = request["approvalId"]
        if approval_id in self._used_approvals:
            used = self._used_approvals[approval_id]
            if used["op"] == op:
                return copy.deepcopy(used["result"])
            raise _problem(409, "APPROVAL_ALREADY_USED", f"approvalId {approval_id}는 다른 변경에 이미 쓰였습니다.")
        if self._approval(approval_id, request["taskId"], role) is None:
            raise _problem(403, "APPROVAL_REQUIRED",
                           f"approvalId {approval_id}는 이 작업의 {role} 승인이 아닙니다. 승인 후 다시 호출합니다.")
        return None

    def _use_approval(self, approval_id: str, op: Json, result: Json, audit_type: str, target: str, detail: str) -> Json:
        self._used_approvals[approval_id] = {"op": op, "result": copy.deepcopy(result)}
        role = self._approved[approval_id]["role"]
        self._audit.append({"auditId": f"AU-{5502 + len(self._audit)}", "occurredAt": _iso(self._now),
                            "type": audit_type,
                            "actor": {"role": role, "display": "관리자" if role == "MANAGER" else "기사"},
                            "targetId": target, "detail": detail})
        return result

    # ── 조회 ─────────────────────────────────────────────
    async def risk_stations(self, region_id, horizon_h, limit):
        if region_id != REGION_ID:
            raise _problem(404, "REGION_NOT_FOUND", f"'{region_id}' 권역이 없습니다. 사용할 수 있는 권역: {REGION_ID}",
                           [REGION_ID])
        rows = []
        for sid, s in STATIONS.items():
            row = self._status(sid, horizon_h)
            if row["risk"] == "NONE":
                continue
            if row["risk"] == "EMPTY":
                needed, weight = -(2 - row["forecastBikes"]), 1.5
                hours = s["bikes"] / -s["netPerH"] if s["netPerH"] < 0 else 0
                empty_at, reasons = _iso(self._now + timedelta(hours=hours)), [f"{horizon_h}시간 안에 빈 대여소 예상"]
            else:
                needed, weight = row["forecastBikes"] - s["docks"], 1.0
                empty_at, reasons = None, [f"{horizon_h}시간 뒤 거치대 수보다 {needed}대 많음"]
            rows.append({**row, "neededBikes": needed, "priority": round(abs(needed) * weight * 10, 1),
                         "emptyAt": empty_at, "reasons": reasons})
        rows.sort(key=lambda r: r["priority"], reverse=True)
        return rows[:limit]

    async def station_detail(self, station_id):
        s = self._station(station_id)
        recent = [r for r in self._reports if r["stationId"] == station_id]
        return {**self._status(station_id), "district": s["district"], "recentReports": copy.deepcopy(recent)}

    async def forecasts(self, station_ids, start, horizon_h):
        start_dt = datetime.fromisoformat(start).replace(minute=0, second=0, microsecond=0)
        rows = []
        for sid in station_ids:
            net = self._station(sid)["netPerH"]
            rows.append({"stationId": sid, "start": _iso(start_dt), "horizonH": horizon_h,
                         "rentals": (4 + max(0, -net)) * horizon_h, "returns": (4 + max(0, net)) * horizon_h,
                         "modelVersion": FORECAST_MODEL})
        return rows

    async def current_plan(self, shift_id):
        self._shift(shift_id)
        return copy.deepcopy(self._plans[self._latest])

    async def truck_status(self, shift_id):
        self._shift(shift_id)
        plan = self._plans[self._latest]
        trucks = []
        for route in plan["routes"]:
            lat, lon, load = TRUCK_POSITIONS[route["truckId"]]
            trucks.append({"truckId": route["truckId"], "capacity": route["capacity"], "load": load,
                           "lat": lat, "lon": lon, "shiftEnd": SHIFT_END,
                           "remainingStops": [copy.deepcopy(s) for s in route["stops"] if s["status"] == "PENDING"]})
        return trucks

    async def field_reports(self, shift_id, since):
        self._shift(shift_id)
        rows = sorted(self._reports, key=lambda r: r["reportedAt"])  # 계약처럼 보고 순서대로
        if since:
            since_dt = datetime.fromisoformat(since)
            rows = [r for r in rows if datetime.fromisoformat(r["reportedAt"]) > since_dt]  # 그 시각은 뺀다 (계약 0.5.0)
        return copy.deepcopy(rows)

    async def audit_events(self, shift_id, since):
        self._shift(shift_id)
        rows = self._audit
        if since:
            since_dt = datetime.fromisoformat(since)
            rows = [r for r in rows if datetime.fromisoformat(r["occurredAt"]) > since_dt]  # 그 시각은 뺀다 (계약 0.5.0)
        return copy.deepcopy(rows)

    # ── 계산 ─────────────────────────────────────────────
    async def solve(self, request):
        self._shift(request["shiftId"])
        for sid in request.get("excludeStationIds", []):
            self._station(sid)
        base = self._plans[self._latest]
        excluded = set(request.get("excludeStationIds", []))
        truck_ids = set(request.get("truckIds") or [r["truckId"] for r in base["routes"]])
        unknown = truck_ids - {r["truckId"] for r in base["routes"]}
        if unknown:
            trucks = [r["truckId"] for r in base["routes"]]
            raise _problem(404, "TRUCK_NOT_FOUND", f"근무조에 없는 트럭: {', '.join(sorted(unknown))}. 배정된 트럭: "
                           f"{', '.join(trucks)}", trucks)
        plan = copy.deepcopy(base)
        plan["version"] = self._latest + 1
        plan["planId"] = _plan_id(plan["version"])
        plan["status"] = "DRAFT"
        diffs = []
        for route in plan["routes"]:
            if route["truckId"] not in truck_ids:
                continue
            before = route["stops"]
            kept = [s for s in before if s["stationId"] not in excluded or s["status"] == "DONE"]
            for s in before:
                if s not in kept:
                    plan["unserved"].append({"stationId": s["stationId"], "stationName": s["stationName"],
                                             "priority": 0.0, "reason": "EXCLUDED"})
            load = route["startLoad"]
            for i, s in enumerate(kept, start=1):
                load += s["bikes"] if s["action"] == "PICKUP" else -s["bikes"]
                s["seq"], s["loadAfter"] = i, max(0, min(load, TRUCK_CAPACITY))
            route["stops"] = kept
            if len(kept) != len(before):
                diffs.append({"truckId": route["truckId"], "before": before, "after": kept,
                              "addedMinutes": -3.0 * (len(before) - len(kept))})
        removed = sum(len(d["before"]) - len(d["after"]) for d in diffs)
        # 변경 등급은 백엔드 규칙(PRD 6.6). 가짜는 빠진 정류장 수와 트럭 수만 본다.
        if request["kind"] == "PLAN":
            grade = None
        elif not diffs:
            grade = "NONE"
        else:
            grade = "MINOR" if removed <= 2 and len(diffs) == 1 else "MAJOR"
        plan["explanation"] = f"대여소 {len(excluded)}곳을 빼고 다시 계산했습니다. (가짜 데이터)"
        self._plans[plan["version"]] = plan
        self._latest = plan["version"]
        candidate_id = f"CAND-{self._next('candidate'):04d}"
        # 승인 순서도 백엔드가 등급으로 정한다 (specs internal-api v0.2 PlanCandidate.requiredApprovers).
        required = {"MINOR": ["DRIVER"], "MAJOR": ["MANAGER", "DRIVER"]}.get(grade or "", [])
        candidate = {"candidateId": candidate_id, "status": "OK", "plan": plan, "grade": grade,
                     "requiredApprovers": required, "diffs": diffs}
        self._candidates[candidate_id] = candidate
        self._latest_candidate = candidate_id
        return copy.deepcopy(candidate)

    # ── 변경 (approvalId 필요) ───────────────────────────
    async def add_constraint(self, request):
        op = {"kind": "constraint", **{k: v for k, v in request.items() if k != "approvalId"}}
        if (replay := self._check_approval(request, op, "MANAGER")) is not None:
            return replay
        self._shift(request["shiftId"])
        for sid in request["stationIds"]:
            self._station(sid)
        if request["type"] == "TARGET_ADJUST" and abs(request.get("value") or 0) > 10:
            raise _problem(400, "VALUE_OUT_OF_RANGE", "목표 재고 가감 폭은 10대 이내여야 합니다 (가짜 운영 상한).")
        saved = {**request, "constraintId": f"CN-{self._next('constraint')}"}
        self._constraints.append(saved)
        return self._use_approval(request["approvalId"], op, copy.deepcopy(saved), "CONSTRAINT_ADDED",
                                  saved["constraintId"], f"{request['type']} {', '.join(request['stationIds'])}: {request['reason']}")

    async def activate_plan(self, plan_id, approval):
        op = {"kind": "activate", "planId": plan_id}
        if (replay := self._check_approval(approval, op, "MANAGER")) is not None:
            return replay
        plan = next((p for p in self._plans.values() if p["planId"] == plan_id), None)
        if plan is None:
            raise _problem(404, "PLAN_NOT_FOUND", f"'{plan_id}' 계획이 없습니다. 최신 계획: {_plan_id(self._latest)}",
                           [_plan_id(self._latest)])
        if plan["version"] != self._latest or plan["status"] == "ACTIVE":
            raise _problem(409, "PLAN_SUPERSEDED", f"'{plan_id}'는 이미 활성화됐거나 더 새로운 계획이 있습니다. "
                           f"최신 계획: {_plan_id(self._latest)}", [_plan_id(self._latest)])
        for p in self._plans.values():
            if p["status"] == "ACTIVE":
                p["status"] = "CLOSED"
        plan["status"] = "ACTIVE"
        return self._use_approval(approval["approvalId"], op, copy.deepcopy(plan), "PLAN_ACTIVATED", plan_id,
                                  f"계획 v{plan['version']} 활성화")

    async def apply_route(self, truck_id, request):
        cid = request["candidateId"]
        op = {"kind": "apply", "truckId": truck_id, "candidateId": cid}
        # approvalId는 마지막 카드(기사 수락)다.
        if (replay := self._check_approval(request, op, "DRIVER")) is not None:
            return replay
        candidate = self._candidates.get(cid)
        if candidate is None:
            raise _problem(404, "CANDIDATE_NOT_FOUND", f"'{cid}' 후보가 없습니다. request_route_plan으로 후보를 먼저 만듭니다.")
        if cid != self._latest_candidate:
            raise _problem(409, "CANDIDATE_SUPERSEDED", f"'{cid}'보다 새로운 후보가 있습니다. "
                           f"최신 후보: {self._latest_candidate}", [self._latest_candidate])
        if not candidate["requiredApprovers"]:
            raise _problem(409, "CANDIDATE_NO_CHANGE", f"'{cid}'는 변경 등급이 NONE이라 적용할 것이 없습니다.")
        # 앞선 카드(MAJOR의 관리자 승인)도 같은 작업·같은 후보에서 승인됐는지 확인한다.
        for role in candidate["requiredApprovers"][:-1]:
            if not any(r["role"] == role and r["taskId"] in (None, request["taskId"]) and r["candidateId"] in (None, cid)
                       for r in self._approved.values()):
                raise _problem(403, "APPROVAL_REQUIRED",
                               f"후보 {cid}의 승인 순서 {candidate['requiredApprovers']} 중 {role} 승인이 없습니다.")
        route = next((r for r in candidate["plan"]["routes"] if r["truckId"] == truck_id), None)
        if route is None:
            trucks = [r["truckId"] for r in candidate["plan"]["routes"]]
            raise _problem(404, "TRUCK_NOT_FOUND", f"'{truck_id}' 트럭이 후보에 없습니다. 후보의 트럭: {', '.join(trucks)}",
                           trucks)
        return self._use_approval(request["approvalId"], op, copy.deepcopy(route), "ROUTE_CHANGED", truck_id,
                                  f"{truck_id} 경로 변경 적용 ({request['candidateId']})")
