"""Capacitated truck routes with fixed current loads, positions and shift budgets."""
from __future__ import annotations

from time import perf_counter

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
    timings: dict[str, float] | None = None,
) -> Plan:
    """Return a candidate plan; this function never activates or stores it.

    Costs are metres. A priority point costs penalty_per_bike_m when dropped.
    Default search is greedy descent with a fixed solution count; it also stops
    at a local optimum. The legacy time_limit_s argument is an optional emergency
    wall-clock cap, NOT a reproducibility guarantee. Every truck starts with its
    supplied load and must return to the depot.
    """
    if timings is not None:
        timings.clear()
        timings.update(prepare_problem=0.0, build_model=0.0, search=0.0, extract_plan=0.0)
    last_checkpoint = perf_counter() if timings is not None else 0.0

    def checkpoint(stage):
        nonlocal last_checkpoint
        if timings is not None:
            now = perf_counter()
            timings[stage] = now - last_checkpoint
            last_checkpoint = now

    require_number("penalty_per_bike_m", penalty_per_bike_m)
    require_int("truck_fixed_cost_m", truck_fixed_cost_m)
    if time_limit_s is not None:
        require_int("time_limit_s", time_limit_s, minimum=1)
    problem = prepare_problem(
        depot, stations, imbalances, trucks, travel_time=travel_time, options=options,
        completed_station_ids=completed_station_ids, blocked_station_ids=blocked_station_ids,
    )
    checkpoint("prepare_problem")

    def finish(routes, served, *, no_solution=False):
        plan = problem.plan(routes, served, "ortools", no_solution=no_solution)
        checkpoint("extract_plan")
        return plan

    if not problem.imbalances or not problem.trucks:
        return finish([], set())

    demands = [0] + [i.delta for i in problem.imbalances] + [0] * len(problem.trucks)
    manager = pywrapcp.RoutingIndexManager(
        len(demands), len(problem.trucks), problem.starts, [0] * len(problem.trucks),
    )
    routing = pywrapcp.RoutingModel(manager)

    # Node-based native evaluators avoid crossing into Python for every search arc.
    # Copy distances before clearing start->depot: provider/cache matrices stay immutable.
    distances = [list(row) for row in problem.matrix.distances_m]
    service = [problem.service_s(node) for node in range(len(demands))]
    durations = [[service[node] + value for value in row]
                 for node, row in enumerate(problem.matrix.durations_s)]
    for start in problem.starts:
        # Starts are dedicated nodes, and depot 0 is the end only. This is an
        # unused vehicle, not a service route's mandatory physical return leg.
        distances[start][0] = 0
        durations[start][0] = 0
    distance_idx = routing.RegisterTransitMatrix(distances)
    routing.SetArcCostEvaluatorOfAllVehicles(distance_idx)
    routing.SetFixedCostOfAllVehicles(truck_fixed_cost_m)

    load_idx = routing.RegisterUnaryTransitVector(demands)
    routing.AddDimensionWithVehicleCapacity(load_idx, 0, [t.capacity for t in problem.trucks], False, "Load")
    load_dim = routing.GetDimensionOrDie("Load")

    time_idx = routing.RegisterTransitMatrix(durations)
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
    checkpoint("build_model")
    solution = routing.SolveWithParameters(params)
    checkpoint("search")
    if solution is None:
        return finish([], set(), no_solution=True)

    routes: list[Route] = []
    served: set[int] = set()
    for vehicle, truck in enumerate(problem.trucks):
        index = routing.Start(vehicle)
        if routing.IsEnd(solution.Value(routing.NextVar(index))):
            continue
        route = Route(truck_id=truck.id, start_load=truck.load)
        while not routing.IsEnd(index):
            nxt = solution.Value(routing.NextVar(index))
            route.distance_m += distances[manager.IndexToNode(index)][manager.IndexToNode(nxt)]
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
    return finish(routes, served)
