# Author: Jung Minkyu
# Date: 20OCT2026
# Purpose: Entry point that runs the mock pipeline and compares greedy vs OR-Tools plans.

"""Run the end-to-end mock pipeline:  python -m rebalancing --hour 8 --trucks 3"""
from __future__ import annotations

import argparse

from .baseline import plan_greedy
from .forecast import HeuristicPredictor
from .imbalance import compute_imbalances
from .mock_data import DEPOT, make_stations, make_trucks
from .models import Plan
from .planner import plan_routes


def print_plan(plan: Plan, names: dict[int, str]) -> None:
    print(f"[{plan.solver}] trucks used={len(plan.routes)}  "
          f"distance={plan.total_distance_m / 1000:.1f} km  "
          f"bikes moved={plan.bikes_moved}  unserved bikes={plan.unserved_bikes}")
    for r in plan.routes:
        steps = " -> ".join(
            f"{names[s.station_id]}({'+' if s.action == 'pickup' else '-'}{s.bikes})" for s in r.stops
        )
        print(f"  truck {r.truck_id} (start {r.start_load}, {r.distance_m / 1000:.1f} km): depot -> {steps} -> depot")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hour", type=int, default=8, help="hour of day to plan for (0-23)")
    ap.add_argument("--trucks", type=int, default=3)
    ap.add_argument("--truck-capacity", type=int, default=20)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    stations, kinds = make_stations(seed=args.seed)
    trucks = make_trucks(args.trucks, args.truck_capacity)
    imbalances = compute_imbalances(stations, HeuristicPredictor(kinds, seed=args.seed), args.hour)
    names = {s.id: s.name for s in stations}

    pick = sum(i.delta for i in imbalances if i.delta > 0)
    drop = -sum(i.delta for i in imbalances if i.delta < 0)
    print(f"hour={args.hour}  imbalanced stations={len(imbalances)}/{len(stations)}  "
          f"surplus={pick}  shortage={drop}\n")

    print_plan(plan_greedy(DEPOT, stations, imbalances, trucks), names)
    print()
    print_plan(plan_routes(DEPOT, stations, imbalances, trucks), names)


if __name__ == "__main__":
    main()
