"""Truck routing with Google OR-Tools.

Model: a vehicle-routing problem where every station is a node with a *signed
demand* (+n = pick n bikes up, -n = drop n bikes off). A cumulative "Load"
dimension tracks how many bikes are on the truck and is bounded to [0, capacity],
which is what forces a valid pickup/dropoff ordering (a truck can't drop bikes
it hasn't picked up yet). Stations that are too costly to reach may be skipped
at a penalty (disjunction), so the solver always returns a plan.
"""
from __future__ import annotations

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from .geo import distance_matrix
from .models import Imbalance, Plan, Route, Station, Stop, Truck

DEPOT = 0


def plan_routes(
    depot: tuple[float, float],
    stations: list[Station],
    imbalances: list[Imbalance],
    trucks: list[Truck],
    *,
    time_limit_s: int = 5,
    penalty_per_bike_m: int = 3_000,
    truck_fixed_cost_m: int = 2_000,
) -> Plan:
    """penalty_per_bike_m / truck_fixed_cost_m are expressed in metres so they trade
    off directly against driving distance: skipping one unbalanced bike is
    "worth" driving 3 km; dispatching one more truck is worth 2 km."""
    if not imbalances:
        return Plan(routes=[], unserved=[], solver="ortools")

    by_id = {s.id: s for s in stations}
    # node 0 = depot, node i = imbalances[i - 1]
    points = [depot] + [(by_id[i.station_id].lat, by_id[i.station_id].lon) for i in imbalances]
    demand = [0] + [i.delta for i in imbalances]
    dist = distance_matrix(points)

    manager = pywrapcp.RoutingIndexManager(len(points), len(trucks), DEPOT)
    routing = pywrapcp.RoutingModel(manager)

    def distance_cb(a: int, b: int) -> int:
        return dist[manager.IndexToNode(a)][manager.IndexToNode(b)]

    transit = routing.RegisterTransitCallback(distance_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(transit)
    routing.SetFixedCostOfAllVehicles(truck_fixed_cost_m)

    def load_cb(index: int) -> int:
        return demand[manager.IndexToNode(index)]

    load_idx = routing.RegisterUnaryTransitCallback(load_cb)
    routing.AddDimensionWithVehicleCapacity(
        load_idx,
        0,  # no slack
        [t.capacity for t in trucks],
        False,  # trucks may leave the depot already loaded
        "Load",
    )

    for node in range(1, len(points)):
        routing.AddDisjunction([manager.NodeToIndex(node)], penalty_per_bike_m * abs(demand[node]))

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.seconds = time_limit_s

    solution = routing.SolveWithParameters(params)
    if solution is None:
        return Plan(routes=[], unserved=list(imbalances), solver="ortools")

    load_dim = routing.GetDimensionOrDie("Load")
    routes: list[Route] = []
    served: set[int] = set()
    for v, truck in enumerate(trucks):
        index = routing.Start(v)
        if routing.IsEnd(solution.Value(routing.NextVar(index))):
            continue  # truck unused
        route = Route(truck_id=truck.id, start_load=solution.Value(load_dim.CumulVar(index)))
        while not routing.IsEnd(index):
            nxt = solution.Value(routing.NextVar(index))
            route.distance_m += distance_cb(index, nxt)
            node = manager.IndexToNode(index)
            if node != DEPOT:
                served.add(node)
                imb = imbalances[node - 1]
                route.stops.append(
                    Stop(imb.station_id, imb.action, abs(imb.delta), solution.Value(load_dim.CumulVar(nxt)))
                )
            index = nxt
        routes.append(route)

    unserved = [imb for n, imb in enumerate(imbalances, start=1) if n not in served]
    return Plan(routes=routes, unserved=unserved, solver="ortools")
