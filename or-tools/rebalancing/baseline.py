"""Nearest feasible station baseline using the same physical constraints as OR-Tools."""
from __future__ import annotations

from .models import Imbalance, Plan, Route, RoutingStation, Station, Stop, Truck
from .problem import PlanningOptions, prepare_problem
from .travel_time import LatLon, TravelTime


def plan_greedy(
    depot: LatLon,
    stations: list[Station | RoutingStation],
    imbalances: list[Imbalance],
    trucks: list[Truck],
    *,
    travel_time: TravelTime | None = None,
    options: PlanningOptions | None = None,
    completed_station_ids: frozenset[int] = frozenset(),
    blocked_station_ids: frozenset[int] = frozenset(),
) -> Plan:
    problem = prepare_problem(
        depot, stations, imbalances, trucks, travel_time=travel_time, options=options,
        completed_station_ids=completed_station_ids, blocked_station_ids=blocked_station_ids,
    )
    todo = dict(enumerate(problem.imbalances, start=1))
    routes: list[Route] = []
    served: set[int] = set()
    distances, durations = problem.matrix.distances_m, problem.matrix.durations_s
    for vehicle, truck in enumerate(problem.trucks):
        position, load, elapsed = problem.starts[vehicle], truck.load, 0
        route = Route(truck_id=truck.id, start_load=load)
        while True:
            feasible = [
                node for node, imbalance in todo.items()
                if 0 <= load + imbalance.delta <= truck.capacity
                and problem.fixed_vehicles.get(node, vehicle) == vehicle
                and elapsed + durations[position][node] + problem.service_s(node) + durations[node][0]
                <= truck.available_time_s
            ]
            if not feasible:
                break
            node = min(feasible, key=lambda n: (distances[position][n], todo[n].station_id))
            imbalance = todo.pop(node)
            arrival = elapsed + durations[position][node]
            elapsed = arrival + problem.service_s(node)
            load += imbalance.delta
            route.distance_m += distances[position][node]
            route.stops.append(Stop(imbalance.station_id, imbalance.action, abs(imbalance.delta), load, arrival, elapsed))
            served.add(imbalance.station_id)
            position = node
        if route.stops:
            route.distance_m += distances[position][0]
            route.duration_s = elapsed + durations[position][0]
            routes.append(route)
    return problem.plan(routes, served, "greedy")
