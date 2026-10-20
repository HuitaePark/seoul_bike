"""Repeatable local engine measurement; NOT the end-to-end 10-second acceptance test."""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from hashlib import sha256
import json
from math import ceil
from statistics import median
from time import perf_counter

import ortools

from .forecast import HeuristicPredictor
from .imbalance import compute_imbalances
from .mock_data import DEPOT, make_stations, make_trucks
from .models import Imbalance
from .planner import plan_routes
from .problem import PlanningOptions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stations", type=int, default=200)
    parser.add_argument("--trucks", type=int, default=5)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--solution-limit", type=int, default=50)
    parser.add_argument("--all-candidates", action="store_true", help="stress input: every station requires service")
    args = parser.parse_args()
    if args.stations < 1 or args.trucks < 1 or args.repeats < 1 or args.solution_limit < 1:
        parser.error("stations, trucks, repeats and solution-limit must be positive")
    stations, kinds = make_stations(per_cluster=ceil(args.stations / 5), seed=args.seed)
    stations = stations[:args.stations]
    if args.all_candidates:
        stations = [replace(s, bikes=5 if s.id % 2 == 0 else 0) for s in stations]
    trucks = make_trucks(args.trucks)
    imbalances = (
        [Imbalance(s.id, 5 if s.id % 2 == 0 else -5) for s in stations]
        if args.all_candidates else compute_imbalances(stations, HeuristicPredictor(kinds, seed=args.seed), 8)
    )
    options = PlanningOptions(solution_limit=args.solution_limit)
    elapsed, fingerprints = [], []
    for _ in range(args.repeats):
        started = perf_counter()
        plan = plan_routes(DEPOT, stations, imbalances, trucks, options=options)
        elapsed.append(perf_counter() - started)
        encoded = json.dumps(asdict(plan), sort_keys=True).encode()
        fingerprints.append(sha256(encoded).hexdigest())
    report = {
        "measurement": "local engine including matrix preparation; excludes Kafka, OSRM HTTP, backend and LLM",
        "ortools_version": ortools.__version__,
        "seed": args.seed,
        "input_stations": len(stations),
        "candidate_stations": len(imbalances),
        "trucks": len(trucks),
        "scenario": "all-candidates" if args.all_candidates else "mock-forecast",
        "solution_limit": args.solution_limit,
        "elapsed_s": elapsed,
        "median_s": median(elapsed),
        "max_s": max(elapsed),
        "same_plan_each_run": len(set(fingerprints)) == 1,
        "plan_sha256": fingerprints[0],
        "used_trucks": len(plan.routes),
        "required_bikes": sum(abs(i.delta) for i in imbalances),
        "handled_bikes": plan.bikes_moved,
        "unserved_bikes": plan.unserved_bikes,
        "distance_m": plan.total_distance_m,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["same_plan_each_run"]:
        raise SystemExit("fixed-input repeatability check failed")


if __name__ == "__main__":
    main()
