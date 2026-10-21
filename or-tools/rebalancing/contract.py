"""AsyncAPI 0.5 adapter (payload schemaVersion 1); no Kafka or database I/O."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
import re
from time import perf_counter
from uuid import UUID, uuid4

from .baseline import plan_greedy
from .models import Imbalance, RoutingStation, Truck
from .osrm import TravelTimeError
from .planner import plan_routes
from .problem import PlanningOptions, require_int, require_number
from .travel_time import LatLon, TravelTime, validate_point


FAILURE_CODES = frozenset({"INVALID_REQUEST", "NO_SOLUTION", "OSRM_BUDGET_EXCEEDED", "OSRM_TIMEOUT",
                           "OSRM_DATA_CHANGED", "OSRM_UNREACHABLE", "OSRM_INVALID_RESPONSE",
                           "OSRM_HTTP_ERROR", "OSRM_NETWORK_ERROR", "OSRM_REJECTED"})
UNSERVED_REASONS = frozenset({"BLOCKED", "NO_TRUCKS", "CAPACITY_EXCEEDED", "NOT_SELECTED", "NO_SOLUTION"})


def reject_constant(value):
    raise ValueError(f"non-JSON number: {value}")


def _text(name: str, value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _timestamp(name: str, value: str) -> datetime:
    try:
        text = _text(name, value)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})", text):
            raise ValueError("date-time must use the RFC 3339 form")
        parsed = datetime.fromisoformat(text.replace("z", "Z"))
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO 8601 timestamp with timezone") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} requires a timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp requires a timezone")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _uuid(name: str, value: str) -> str:
    try:
        parsed = UUID(_text(name, value))
        if str(parsed) != value.lower():
            raise ValueError("UUID must use its hyphenated form")
    except ValueError as error:
        raise ValueError(f"{name} must be a UUID") from error
    return value


def _object(name: str, value) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _array(name: str, value) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value


def _minutes(name: str, value) -> int:
    require_number(name, value)
    seconds = int((Decimal(str(value)) * 60).to_integral_value(rounding=ROUND_CEILING))
    require_int(name + " in seconds", seconds)
    return seconds


@dataclass(frozen=True)
class SolveContext:
    """Validated depot and stable calculation time from SolveRequest."""

    depot: LatLon
    as_of: datetime

    def __post_init__(self):
        validate_point("context.depot", self.depot)
        _iso(self.as_of)

    @classmethod
    def from_request(cls, payload: dict):
        depot = _object("depot", payload.get("depot"))
        return cls((depot.get("lat"), depot.get("lon")), _timestamp("asOf", payload.get("asOf")))


def _correlation(payload: dict) -> dict:
    _object("SolveRequest", payload)
    _uuid("eventId", payload.get("eventId"))
    if type(payload.get("schemaVersion")) is not int or payload["schemaVersion"] != 1:
        raise ValueError("schemaVersion must be 1")
    _timestamp("occurredAt", payload.get("occurredAt"))
    result = {"schemaVersion": 1, "shiftId": _text("shiftId", payload.get("shiftId")),
              "requestId": _text("requestId", payload.get("requestId"))}
    if "traceId" in payload:
        if not isinstance(payload["traceId"], str):
            raise ValueError("traceId must be a string")
        result["traceId"] = payload["traceId"]
    return result


def _prepare(payload: dict, context: SolveContext):
    if payload.get("kind") not in ("PLAN", "REPLAN"):
        raise ValueError("kind must be PLAN or REPLAN")
    station_rows = _array("stations", payload.get("stations"))
    truck_rows = _array("trucks", payload.get("trucks"))
    station_by_id, truck_by_id = {}, {}
    for row in station_rows:
        row = _object("station", row)
        station_id = _text("stationId", row.get("stationId"))
        if station_id in station_by_id:
            raise ValueError("duplicate stationId")
        station_by_id[station_id] = row
    for row in truck_rows:
        row = _object("truck", row)
        truck_id = _text("truckId", row.get("truckId"))
        if truck_id in truck_by_id:
            raise ValueError("duplicate truckId")
        truck_by_id[truck_id] = row
    station_ids, truck_ids = sorted(station_by_id), sorted(truck_by_id)
    station_index = {value: i for i, value in enumerate(station_ids)}
    stations, imbalances, trucks = [], [], []
    for i, station_id in enumerate(station_ids):
        row = station_by_id[station_id]
        point = (row.get("lat"), row.get("lon"))
        validate_point("station position", point)
        needed = row.get("neededBikes")
        if type(needed) is not int or abs(needed) > 1_000_000_000:
            raise ValueError("neededBikes must be an integer in [-1e9, 1e9]")
        priority = row.get("priority")
        require_number("priority", priority)
        if "kind" in row and row["kind"] not in ("EMPTY", "OVERFLOW"):
            raise ValueError("station kind must be EMPTY or OVERFLOW")
        stations.append(RoutingStation(i, station_id, *point))
        if needed != 0:
            imbalances.append(Imbalance(i, needed, priority))
    for i, truck_id in enumerate(truck_ids):
        row = truck_by_id[truck_id]
        capacity, load = row.get("capacity"), row.get("load")
        require_int("truck capacity", capacity, minimum=1)
        require_int("truck load", load)
        if load > capacity:
            raise ValueError("truck load exceeds capacity")
        point = (row.get("lat"), row.get("lon"))
        validate_point("truck position", point)
        until = _timestamp("availableUntil", row.get("availableUntil"))
        delta = until - context.as_of
        # datetime subtraction is exact to microseconds; remaining time rounds
        # down while service rounds up, so the shift end cannot be overrun.
        available = delta.days * 86400 + delta.seconds
        require_int("remaining shift seconds", available)
        fixed = []
        for station_id in _array("fixedStationIds", row.get("fixedStationIds", [])):
            _text("fixed stationId", station_id)
            if station_id not in station_index:
                raise ValueError("fixedStationIds contains an unknown stationId")
            fixed.append(station_index[station_id])
        trucks.append(Truck(i, capacity, load, point, available, tuple(fixed)))
    blocked = set()
    for station_id in _array("blockedStationIds", payload.get("blockedStationIds", [])):
        _text("blocked stationId", station_id)
        if station_id not in station_index:
            raise ValueError("blockedStationIds contains an unknown stationId")
        blocked.add(station_index[station_id])
    params = _object("params", payload.get("params"))
    limit = params.get("solutionLimit")
    require_int("solutionLimit", limit, minimum=1)
    scale = params.get("penaltyScale", 3000)
    require_number("penaltyScale", scale)
    options = PlanningOptions(base_service_s=_minutes("serviceMinutesBase", params.get("serviceMinutesBase", 3)),
                              per_bike_service_s=_minutes("serviceMinutesPerBike", params.get("serviceMinutesPerBike", 0.5)),
                              solution_limit=limit)
    return stations, imbalances, trucks, frozenset(blocked), options, scale, station_ids, truck_ids


def solve_request(payload: dict, *, travel_time: TravelTime,
                  policy: str = "ortools", result_event_id: str | None = None,
                  result_time: datetime | None = None) -> dict:
    """Return a common SolveResult. Unknown additive fields are ignored.

    A malformed envelope/requestId raises ValueError because no valid correlated
    result can be formed. With a valid envelope, invalid planning input returns
    FAILED. The function does not consume, publish, persist or deduplicate Kafka.
    """
    if travel_time is None:
        raise ValueError("an explicit travel_time provider is required")
    if policy not in ("ortools", "greedy"):
        raise ValueError("policy must be ortools or greedy")
    result = _correlation(payload)
    result["eventId"] = _uuid("result eventId", result_event_id if result_event_id is not None else str(uuid4()))
    if UUID(result["eventId"]) == UUID(payload["eventId"]):
        raise ValueError("result eventId must differ from request eventId")
    started = perf_counter()
    try:
        context = SolveContext.from_request(payload)
        stations, jobs, trucks, blocked, options, scale, station_ids, truck_ids = _prepare(payload, context)
        kwargs = {"travel_time": travel_time, "options": options, "blocked_station_ids": blocked}
        if policy == "ortools":
            plan = plan_routes(context.depot, stations, jobs, trucks, penalty_per_bike_m=scale, **kwargs)
        else:
            plan = plan_greedy(context.depot, stations, jobs, trucks, **kwargs)
        routes = []
        for route in plan.routes:
            routes.append({"truckId": truck_ids[route.truck_id], "capacity": trucks[route.truck_id].capacity,
                           "startLoad": route.start_load, "stops": [
                               {"seq": seq, "stationId": station_ids[stop.station_id], "action": stop.action.upper(),
                                "bikes": stop.bikes, "loadAfter": stop.load_after,
                                "eta": _iso(context.as_of + timedelta(seconds=stop.arrival_s)), "status": "PENDING"}
                               for seq, stop in enumerate(route.stops, start=1)]})
        result.update(status="FAILED" if plan.status == "no_solution" else "OK", routes=routes,
                      unserved=[{"stationId": station_ids[job.station_id], "priority": job.priority,
                                 "reason": plan.unserved_reasons[job.station_id].upper()} for job in plan.unserved],
                      stats={"solveMs": max(0, int((perf_counter() - started) * 1000)), "bikesMoved": plan.bikes_moved})
        if plan.status == "no_solution":
            result.update(code="NO_SOLUTION", retryable=False,
                          error="solver returned no solution under the configured search")
    except ValueError as error:
        result.update(status="FAILED", code="INVALID_REQUEST", retryable=False, error=str(error))
    except TravelTimeError as error:
        result.update(status="TIMEOUT" if error.code in ("budget_exceeded", "timeout") else "FAILED",
                      code="OSRM_REJECTED" if error.code == "osrm_error" else f"OSRM_{error.code.upper()}",
                      retryable=error.retryable, error=str(error))
    result["occurredAt"] = _iso(result_time if result_time is not None else datetime.now(timezone.utc))
    return result
