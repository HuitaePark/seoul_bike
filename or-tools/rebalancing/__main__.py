# Author: Jung Minkyu
# Date: 20OCT2026
# Purpose: Entry point that runs the mock pipeline and compares greedy vs OR-Tools plans.

"""Run the end-to-end mock pipeline:  python -m rebalancing --hour 8 --trucks 3"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from datetime import datetime

from .baseline import plan_greedy
from .forecast import HeuristicPredictor, forecast_window
from .imbalance import compute_imbalances
from .mock_data import DEPOT, make_stations, make_trucks
from .models import Plan
from .matrix_cache import CachedTravelTime
from .osrm import OsrmTravelTime, TravelTimeError
from .planner import plan_routes
from .problem import PlanningOptions
from .travel_time import HaversineTravelTime


def print_plan(plan: Plan, names: dict[int, str]) -> None:
    print(f"[{plan.solver}] trucks used={len(plan.routes)}  "
          f"distance={plan.total_distance_m / 1000:.1f} km  "
          f"bikes moved={plan.bikes_moved}  unserved bikes={plan.unserved_bikes}  status={plan.status}")
    for r in plan.routes:
        steps = " -> ".join(
            f"{names[s.station_id]}({'+' if s.action == 'pickup' else '-'}{s.bikes})" for s in r.stops
        )
        print(f"  truck {r.truck_id} (start {r.start_load}, {r.distance_m / 1000:.1f} km, "
              f"{r.duration_s / 60:.1f} min): depot -> {steps} -> depot")
    for station_id, reason in plan.unserved_reasons.items():
        print(f"  unserved {names[station_id]}: {reason}")


def main() -> None:
    ap = argparse.ArgumentParser()
    when = ap.add_mutually_exclusive_group()
    when.add_argument("--hour", type=int, default=8, help="mock hour on fixed 2026-10-20 KST (0-23)")
    when.add_argument("--start", help="explicit whole-hour ISO datetime; naive values mean KST")
    ap.add_argument("--horizon-h", type=int, default=1, choices=range(1, 6), help="forecast hours (default 1)")
    ap.add_argument("--trucks", type=int, default=3)
    ap.add_argument("--truck-capacity", type=int, default=15)
    ap.add_argument("--start-load", type=int, default=0)
    ap.add_argument("--shift-minutes", type=int, default=180)
    ap.add_argument("--solution-limit", type=int, default=50)
    ap.add_argument("--speed-kph", type=float, default=30, help="development travel estimate, not live traffic")
    ap.add_argument("--osrm-url", help="OSRM server URL; omit to use the development estimate")
    ap.add_argument("--osrm-data-version", default="unversioned", help="road/profile/engine version; required for disk cache")
    ap.add_argument("--osrm-max-locations", type=int, default=100)
    ap.add_argument("--osrm-timeout-seconds", type=float, default=3)
    ap.add_argument("--osrm-budget-seconds", type=float, default=8)
    ap.add_argument("--osrm-snap-radius-metres", type=float, default=1000, help="maximum road snapping search radius")
    ap.add_argument("--matrix-cache-dir", help="dedicated cache directory (up to 16 matrices, TTL 1 hour)")
    ap.add_argument("--operating-limit", type=int, help="shared operating target for this demo, not a hard dock cap")
    ap.add_argument("--json", action="store_true", help="print the two plans as JSON")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    if not 0 <= args.hour <= 23 or args.trucks < 0 or args.shift_minutes < 0:
        ap.error("hour must be 0..23, trucks and shift-minutes must be non-negative")
    if args.matrix_cache_dir and (not args.osrm_url or args.osrm_data_version == "unversioned"):
        ap.error("disk cache requires --osrm-url and an explicit --osrm-data-version")

    if args.start and "T" not in args.start and " " not in args.start:
        ap.error("--start must include both a date and a time")
    try:
        start = forecast_window(datetime.fromisoformat(args.start) if args.start
                                else datetime(2026, 10, 20, args.hour), args.horizon_h)
    except ValueError as error:
        ap.error(str(error))
    args.hour = start.hour

    stations, kinds = make_stations(seed=args.seed)
    if args.operating_limit is not None:
        stations = [replace(s, operating_limit=args.operating_limit) for s in stations]
    trucks = [replace(t, load=args.start_load, available_time_s=args.shift_minutes * 60)
              for t in make_trucks(args.trucks, args.truck_capacity)]
    try:
        provider = HaversineTravelTime(args.speed_kph)
        if args.osrm_url:
            road = OsrmTravelTime(args.osrm_url, data_version=args.osrm_data_version,
                                  max_locations=args.osrm_max_locations, timeout_s=args.osrm_timeout_seconds,
                                  budget_s=args.osrm_budget_seconds, snap_radius_m=args.osrm_snap_radius_metres)
            provider = CachedTravelTime(road, namespace=road.cache_namespace, cache_dir=args.matrix_cache_dir)
        imbalances = compute_imbalances(stations, HeuristicPredictor(kinds, seed=args.seed), start, args.horizon_h)
        kwargs = {"options": PlanningOptions(solution_limit=args.solution_limit),
                  "travel_time": provider}
        plans = [plan_greedy(DEPOT, stations, imbalances, trucks, **kwargs),
                 plan_routes(DEPOT, stations, imbalances, trucks, **kwargs)]
    except ValueError as error:
        ap.error(str(error))
    except TravelTimeError as error:
        ap.error(f"travel time [{error.code}, retryable={error.retryable}]: {error}")
    travel_settings = ({"travel_source": "osrm", "data_version": args.osrm_data_version,
                        "cache_hits": provider.hits, "cache_misses": provider.misses}
                       if args.osrm_url else {"travel_source": "haversine-estimate", "speed_kph": args.speed_kph})
    if args.json:
        print(json.dumps({"hour": args.hour, "seed": args.seed,
                          "settings": {"forecast_start_kst": start.isoformat(), "forecast_horizon_h": args.horizon_h,
                                       "truck_capacity": args.truck_capacity, "start_load": args.start_load,
                                       "shift_seconds": args.shift_minutes * 60, "solution_limit": args.solution_limit,
                                       **travel_settings},
                          "plans": [asdict(p) for p in plans]},
                         ensure_ascii=False, indent=2))
        return
    names = {s.id: s.name for s in stations}

    pick = sum(i.delta for i in imbalances if i.delta > 0)
    drop = -sum(i.delta for i in imbalances if i.delta < 0)
    print(f"hour={args.hour}  imbalanced stations={len(imbalances)}/{len(stations)}  "
          f"surplus={pick}  shortage={drop}  travel={travel_settings['travel_source']}\n")

    print_plan(plans[0], names)
    print()
    print_plan(plans[1], names)


if __name__ == "__main__":
    main()
