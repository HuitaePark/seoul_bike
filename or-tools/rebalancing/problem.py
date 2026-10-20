"""Shared input validation and route constraints for both dispatch policies."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from .models import Imbalance, Plan, Route, Station, Truck
from .travel_time import HaversineTravelTime, LatLon, TravelMatrix, TravelTime


@dataclass(frozen=True)
class PlanningOptions:
    # PRD initial values; weights are tunable experiment settings, not measured policy.
    base_service_s: int = 180
    per_bike_service_s: int = 30
    empty_weight: int = 2
    overload_weight: int = 1
    solution_limit: int = 50

    def __post_init__(self) -> None:
        for name in ("base_service_s", "per_bike_service_s", "empty_weight", "overload_weight"):
            require_int(name, getattr(self, name), minimum=0)
        require_int("solution_limit", self.solution_limit, minimum=1)


def require_int(name: str, value: int, minimum: int = 0) -> None:
    if type(value) is not int or not minimum <= value <= 1_000_000_000:
        raise ValueError(f"{name} must be an integer in [{minimum}, 1000000000]")


def validate_point(name: str, point: LatLon) -> None:
    if not isinstance(point, (tuple, list)) or len(point) != 2:
        raise ValueError(f"{name} must be (latitude, longitude)")
    lat, lon = point
    if any(type(v) not in (int, float) or not isfinite(v) for v in point):
        raise ValueError(f"{name} has invalid coordinates")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError(f"{name} has invalid coordinates")


@dataclass
class RoutingProblem:
    imbalances: list[Imbalance]
    trucks: list[Truck]
    matrix: TravelMatrix
    starts: list[int]
    blocked: list[Imbalance]
    options: PlanningOptions

    def service_s(self, node: int) -> int:
        if 1 <= node <= len(self.imbalances):
            return self.options.base_service_s + self.options.per_bike_service_s * abs(self.imbalances[node - 1].delta)
        return 0

    def penalty(self, node: int, penalty_per_bike_m: int) -> int:
        imbalance = self.imbalances[node - 1]
        score = imbalance.priority
        if score is None:
            weight = self.options.empty_weight if imbalance.delta < 0 else self.options.overload_weight
            score = abs(imbalance.delta) * weight
        penalty = penalty_per_bike_m * score
        if penalty > 1_000_000_000_000_000:
            raise ValueError("priority penalty exceeds the supported cost range")
        return penalty

    def plan(self, routes: list[Route], served: set[int], solver: str, *, no_solution: bool = False) -> Plan:
        missing = [i for i in self.imbalances if i.station_id not in served]
        unserved = sorted(missing + self.blocked, key=lambda i: i.station_id)
        reasons = {i.station_id: "blocked" for i in self.blocked}
        max_capacity = max((t.capacity for t in self.trucks), default=0)
        for imbalance in missing:
            if not self.trucks:
                code = "no_trucks"
            elif abs(imbalance.delta) > max_capacity:
                code = "capacity_exceeded"
            elif no_solution:
                code = "no_solution"
            else:
                # A dropped node does not prove whether time, load or cost caused it.
                code = "not_selected"
            reasons[imbalance.station_id] = code
        status = "no_solution" if no_solution else ("partial" if unserved else "complete")
        return Plan(routes, unserved, solver, unserved_reasons=reasons, status=status)


def prepare_problem(
    depot: LatLon,
    stations: list[Station],
    imbalances: list[Imbalance],
    trucks: list[Truck],
    *,
    travel_time: TravelTime | None = None,
    options: PlanningOptions | None = None,
    completed_station_ids: frozenset[int] = frozenset(),
    blocked_station_ids: frozenset[int] = frozenset(),
) -> RoutingProblem:
    validate_point("depot", depot)
    by_id = {s.id: s for s in stations}
    if len(by_id) != len(stations):
        raise ValueError("duplicate station IDs")
    for station in stations:
        require_int("station.id", station.id)
        require_int("station.capacity", station.capacity, minimum=1)
        require_int("station.bikes", station.bikes)
        if station.operating_limit is not None:
            require_int("station.operating_limit", station.operating_limit)
        validate_point("station.position", (station.lat, station.lon))
    if len({i.station_id for i in imbalances}) != len(imbalances):
        raise ValueError("duplicate imbalance station IDs")
    for imbalance in imbalances:
        require_int("imbalance.station_id", imbalance.station_id)
        if imbalance.station_id not in by_id:
            raise ValueError(f"unknown station ID: {imbalance.station_id}")
        if type(imbalance.delta) is not int or imbalance.delta == 0 or abs(imbalance.delta) > 1_000_000_000:
            raise ValueError("imbalance.delta must be a non-zero integer within +/-1e9")
        if imbalance.priority is not None:
            require_int("imbalance.priority", imbalance.priority)
    if len({t.id for t in trucks}) != len(trucks):
        raise ValueError("duplicate truck IDs")
    for truck in trucks:
        require_int("truck.id", truck.id)
        require_int("truck.capacity", truck.capacity, minimum=1)
        require_int("truck.load", truck.load)
        if truck.load > truck.capacity:
            raise ValueError("truck.load exceeds capacity")
        require_int("truck.available_time_s", truck.available_time_s)
        if truck.position is not None:
            validate_point("truck.position", truck.position)
    excluded = set(completed_station_ids) | set(blocked_station_ids)
    for station_id in excluded:
        require_int("excluded station ID", station_id)
    if not excluded <= by_id.keys():
        raise ValueError("completed/blocked IDs must belong to the station snapshot")
    active = sorted((i for i in imbalances if i.station_id not in excluded), key=lambda i: i.station_id)
    blocked = sorted(
        (i for i in imbalances if i.station_id in blocked_station_ids and i.station_id not in completed_station_ids),
        key=lambda i: i.station_id,
    )
    ordered_trucks = sorted(trucks, key=lambda t: t.id)
    settings = options or PlanningOptions()
    for imbalance in active:
        if imbalance.delta > by_id[imbalance.station_id].bikes:
            raise ValueError("pickup quantity exceeds the supplied station inventory")
        require_int("stop service time", settings.base_service_s + settings.per_bike_service_s * abs(imbalance.delta))
    points = [depot] + [(by_id[i.station_id].lat, by_id[i.station_id].lon) for i in active]
    starts = []
    for truck in ordered_trucks:
        starts.append(len(points))
        points.append(truck.position if truck.position is not None else depot)
    if active and ordered_trucks:
        matrix = (travel_time or HaversineTravelTime()).matrix(points)
    else:
        matrix = TravelMatrix([[0] * len(points) for _ in points], [[0] * len(points) for _ in points])
    matrix.validate(len(points))
    return RoutingProblem(active, ordered_trucks, matrix, starts, blocked, settings)
