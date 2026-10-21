"""Capacitated truck routes with fixed current loads, positions and shift budgets."""
from __future__ import annotations

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from .models import Imbalance, Plan, Route, RoutingStation, Station, Stop, Truck
from .problem import PlanningOptions, prepare_problem, require_int, require_number
from .travel_time import LatLon, TravelTime


def plan_routes(
    depot: LatLon,
    stations: list[Station | RoutingStation],
    imbalances: list[Imbalance],
    trucks: list[Truck],
    *,
    time_limit_s: int | None = None,
    penalty_per_bike_m: float = 3_000,
    truck_fixed_cost_m: int = 2_000,
    travel_time: TravelTime | None = None,
    options: PlanningOptions | None = None,
    completed_station_ids: frozenset[int] = frozenset(),
    blocked_station_ids: frozenset[int] = frozenset(),
) -> Plan:
    """Return a candidate plan; this function never activates or stores it.

    Costs are metres. A priority point costs penalty_per_bike_m when dropped.
    Default search is greedy descent with a fixed solution count; it also stops
    at a local optimum. The legacy time_limit_s argument is an optional emergency
    wall-clock cap, NOT a reproducibility guarantee. Every truck starts with its
    supplied load and must return to the depot.
    """
    require_number("penalty_per_bike_m", penalty_per_bike_m)
    require_int("truck_fixed_cost_m", truck_fixed_cost_m)
    if time_limit_s is not None:
        require_int("time_limit_s", time_limit_s, minimum=1)
    problem = prepare_problem(
        depot, stations, imbalances, trucks, travel_time=travel_time, options=options,
        completed_station_ids=completed_station_ids, blocked_station_ids=blocked_station_ids,
    )
    if not problem.imbalances or not problem.trucks:
        return problem.plan([], set(), "ortools")

    demands = [0] + [i.delta for i in problem.imbalances] + [0] * len(problem.trucks)
    manager = pywrapcp.RoutingIndexManager(
        len(demands), len(problem.trucks), problem.starts, [0] * len(problem.trucks),
    )
    routing = pywrapcp.RoutingModel(manager)

    def distance_cb(a: int, b: int) -> int:
        if routing.IsStart(a) and routing.IsEnd(b):
            return 0  # unused vehicles do not make a mandatory depot trip
        return problem.matrix.distances_m[manager.IndexToNode(a)][manager.IndexToNode(b)]

    distance_idx = routing.RegisterTransitCallback(distance_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(distance_idx)
    routing.SetFixedCostOfAllVehicles(truck_fixed_cost_m)

    load_idx = routing.RegisterUnaryTransitCallback(lambda i: demands[manager.IndexToNode(i)])
    routing.AddDimensionWithVehicleCapacity(load_idx, 0, [t.capacity for t in problem.trucks], False, "Load")
    load_dim = routing.GetDimensionOrDie("Load")

    def time_cb(a: int, b: int) -> int:
        if routing.IsStart(a) and routing.IsEnd(b):
            return 0
        node_a, node_b = manager.IndexToNode(a), manager.IndexToNode(b)
        return problem.service_s(node_a) + problem.matrix.durations_s[node_a][node_b]

    time_idx = routing.RegisterTransitCallback(time_cb)
    routing.AddDimension(time_idx, 0, max(t.available_time_s for t in problem.trucks), True, "Time")
    time_dim = routing.GetDimensionOrDie("Time")
    for vehicle, truck in enumerate(problem.trucks):
        load_dim.CumulVar(routing.Start(vehicle)).SetValue(truck.load)
        time_dim.CumulVar(routing.End(vehicle)).SetRange(0, truck.available_time_s)

    for node in range(1, len(problem.imbalances) + 1):
        routing.AddDisjunction([manager.NodeToIndex(node)], problem.penalty(node, penalty_per_bike_m))
        if node in problem.fixed_vehicles:
            # -1 keeps a legal unperformed state when service is infeasible.
            routing.VehicleVar(manager.NodeToIndex(node)).SetValues([-1, problem.fixed_vehicles[node]])

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GREEDY_DESCENT
    params.solution_limit = problem.options.solution_limit
    if time_limit_s is not None:
        params.time_limit.seconds = time_limit_s
    solution = routing.SolveWithParameters(params)
    if solution is None:
        return problem.plan([], set(), "ortools", no_solution=True)

    routes: list[Route] = []
    served: set[int] = set()
    for vehicle, truck in enumerate(problem.trucks):
        index = routing.Start(vehicle)
        if routing.IsEnd(solution.Value(routing.NextVar(index))):
            continue
        route = Route(truck_id=truck.id, start_load=truck.load)
        while not routing.IsEnd(index):
            nxt = solution.Value(routing.NextVar(index))
            route.distance_m += distance_cb(index, nxt)
            node = manager.IndexToNode(index)
            if 1 <= node <= len(problem.imbalances):
                imbalance = problem.imbalances[node - 1]
                arrival = solution.Value(time_dim.CumulVar(index))
                route.stops.append(Stop(
                    imbalance.station_id, imbalance.action, abs(imbalance.delta),
                    solution.Value(load_dim.CumulVar(nxt)), arrival,
                    arrival + problem.service_s(node),
                ))
                served.add(imbalance.station_id)
            index = nxt
        route.duration_s = solution.Value(time_dim.CumulVar(routing.End(vehicle)))
        routes.append(route)
    return problem.plan(routes, served, "ortools")
