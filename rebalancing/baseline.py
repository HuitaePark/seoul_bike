# Author: Jung Minkyu
# Date: 20OCT2026
# Purpose: Nearest-neighbour greedy dispatcher used as a yardstick for the OR-Tools plan.

"""Nearest-neighbour greedy dispatcher: what a human dispatcher would roughly do.

Exists only as a yardstick so the value of the OR-Tools plan can be quantified.
"""
from __future__ import annotations

from .geo import haversine_m
from .models import Imbalance, Plan, Route, Station, Stop, Truck


def plan_greedy(
    depot: tuple[float, float],
    stations: list[Station],
    imbalances: list[Imbalance],
    trucks: list[Truck],
) -> Plan:
    by_id = {s.id: s for s in stations}
    todo = {i.station_id: i for i in imbalances}
    routes: list[Route] = []

    for truck in trucks:
        pos, load = depot, truck.capacity // 2
        route = Route(truck_id=truck.id, start_load=load)
        while True:
            feasible = [
                i for i in todo.values()
                if 0 <= load + i.delta <= truck.capacity
            ]
            if not feasible:
                break
            nxt = min(
                feasible,
                key=lambda i: haversine_m(*pos, by_id[i.station_id].lat, by_id[i.station_id].lon),
            )
            s = by_id[nxt.station_id]
            route.distance_m += haversine_m(*pos, s.lat, s.lon)
            load += nxt.delta
            route.stops.append(Stop(nxt.station_id, nxt.action, abs(nxt.delta), load))
            pos = (s.lat, s.lon)
            del todo[nxt.station_id]
        if route.stops:
            route.distance_m += haversine_m(*pos, *depot)
            routes.append(route)

    return Plan(routes=routes, unserved=list(todo.values()), solver="greedy")
