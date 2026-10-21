"""Repeatable local engine measurement; NOT the end-to-end 10-second acceptance test."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import ceil
import platform
from statistics import median
from time import perf_counter

import ortools

from .forecast import HeuristicPredictor
from .imbalance import compute_imbalances
from .matrix_cache import CachedTravelTime
from .mock_data import DEPOT, make_stations, make_trucks
from .models import Imbalance, Station
from .osrm import OsrmTravelTime
from .planner import plan_routes
from .problem import PlanningOptions
from .seeds import REFERENCE_REGIONS, REFERENCE_STATIONS, load_region_seed
from .travel_time import HaversineTravelTime


class TimedTravelTime:
    """Measure matrix calls without changing matrices or solver inputs."""

    def __init__(self, provider):
        self.provider = provider
        self.calls = 0
        self.elapsed_s = 0.0

    def matrix(self, points):
        started = perf_counter()
        self.calls += 1
        try:
            return self.provider.matrix(points)
        finally:
            self.elapsed_s += perf_counter() - started


@dataclass
class BenchmarkCase:
    stations: list
    jobs: list
    trucks: list
    options: PlanningOptions
    completed: frozenset[int] = frozenset()
    blocked: frozenset[int] = frozenset()
    snapshot_elapsed_s: int = 0
    depot: tuple[float, float] = DEPOT
    reference: dict | None = None

    def solve(self, travel, *, timings=None):
        return plan_routes(self.depot, self.stations, self.jobs, self.trucks, options=self.options,
                           travel_time=travel, completed_station_ids=self.completed,
                           blocked_station_ids=self.blocked, timings=timings)

    def fingerprint(self):
        document = {"depot": self.depot, "stations": [asdict(s) for s in self.stations],
                    "jobs": [asdict(j) for j in self.jobs], "trucks": [asdict(t) for t in self.trucks],
                    "options": asdict(self.options), "completed": sorted(self.completed),
                    "blocked": sorted(self.blocked)}
        if self.reference is not None:
            document["reference"] = self.reference
        return fingerprint(document)


def fingerprint(document):
    return sha256(json.dumps(document, sort_keys=True, allow_nan=False).encode()).hexdigest()


def build_case(args, provider):
    if args.region:
        seed = load_region_seed(REFERENCE_REGIONS, REFERENCE_STATIONS, args.region)
        # Real geography, explicitly artificial inventory/workload for scale testing.
        quantities = [min(5, s.docks) for s in seed.stations]
        stations = [Station(int(s.data_id), s.name, *s.position, s.docks, q if i % 2 == 0 else 0)
                    for i, (s, q) in enumerate(zip(seed.stations, quantities))]
        jobs = [Imbalance(s.id, q if i % 2 == 0 else -q) for i, (s, q) in enumerate(zip(stations, quantities))]
        reference = {"region_id": seed.region_id, "wire_id_field": "api_station_id",
                     "id_map_sha256": fingerprint(seed.id_map(source="data")),
                     "regions_sha256": sha256(REFERENCE_REGIONS.read_bytes()).hexdigest(),
                     "stations_sha256": sha256(REFERENCE_STATIONS.read_bytes()).hexdigest(),
                     "inventory": "synthetic; alternating min(5, docks) and zero",
                     "demand": "synthetic; alternating pickup/dropoff min(5, docks)",
                     "depot_basis": "station-coordinate-mean; not an operational depot",
                     "trucks_basis": "explicit benchmark count; not regional trucks_min"}
        case = BenchmarkCase(stations, jobs, make_trucks(args.trucks), PlanningOptions(solution_limit=args.solution_limit),
                             depot=seed.depot, reference=reference)
    else:
        stations, kinds = make_stations(per_cluster=ceil(args.stations / 5), seed=args.seed)
        stations = stations[:args.stations]
        if args.all_candidates:
            stations = [replace(s, bikes=5 if s.id % 2 == 0 else 0) for s in stations]
        jobs = ([Imbalance(s.id, 5 if s.id % 2 == 0 else -5) for s in stations]
                if args.all_candidates else compute_imbalances(stations, HeuristicPredictor(kinds, seed=args.seed), datetime(2026, 10, 20, 8)))
        case = BenchmarkCase(stations, jobs, make_trucks(args.trucks), PlanningOptions(solution_limit=args.solution_limit))
    if args.scenario == "plan":
        return case

    initial = case.solve(provider)
    first_stops = {r.truck_id: r.stops[0] for r in initial.routes if r.stops}
    if not first_stops:
        raise ValueError("replan needs an initial route with at least one completed stop")
    # Each used truck finishes its first stop, then waits there until the slowest
    # first stop finishes. This synthetic snapshot needs no in-flight positions.
    elapsed = max(s.departure_s for s in first_stops.values())
    by_id = {s.id: s for s in stations}
    completed = frozenset(s.station_id for s in first_stops.values())
    changed = []
    for truck in case.trucks:
        stop = first_stops.get(truck.id)
        changed.append(replace(truck, available_time_s=truck.available_time_s - elapsed,
                               load=stop.load_after if stop else truck.load,
                               position=(by_id[stop.station_id].lat, by_id[stop.station_id].lon) if stop else case.depot))
    remaining = [j for j in jobs if j.station_id not in completed]
    blocked = frozenset({remaining[0].station_id}) if remaining else frozenset()
    deltas = {j.station_id: j.delta for j in jobs}
    snapshots = [replace(s, bikes=s.bikes - deltas[s.id]) if s.id in completed else s for s in stations]
    return replace(case, stations=snapshots, trucks=changed, completed=completed, blocked=blocked,
                   snapshot_elapsed_s=elapsed)


def summarize(samples):
    """Nearest-rank p95: sorted samples[ceil(0.95*n)-1], including small samples."""
    return {"samples_s": samples, "median_s": median(samples),
            "p95_s": sorted(samples)[ceil(0.95 * len(samples)) - 1], "max_s": max(samples)}


def measure(case, provider, *, repeats, cache_mode, namespace, profile_stages=False):
    raw = TimedTravelTime(provider)
    cached = CachedTravelTime(raw, namespace=namespace)
    warmup_s = 0.0
    if cache_mode == "warm":
        started = perf_counter()
        case.solve(cached)
        warmup_s = perf_counter() - started
    elapsed, matrix, upstream, non_matrix, fingerprints = [], [], [], [], []
    stages = {name: [] for name in ("prepare_problem", "build_model", "search", "extract_plan")}
    provider_calls = hits = misses = 0
    for _ in range(repeats):
        if cache_mode == "cold":
            cached = CachedTravelTime(raw, namespace=namespace)
        travel = TimedTravelTime(raw if cache_mode == "none" else cached)
        calls_before, raw_before = raw.calls, raw.elapsed_s
        hits_before, misses_before = cached.hits, cached.misses
        started = perf_counter()
        timings = {} if profile_stages else None
        plan = case.solve(travel, timings=timings)
        total = perf_counter() - started
        if timings is not None:
            for name in stages:
                stages[name].append(timings[name])
        elapsed.append(total)
        matrix.append(travel.elapsed_s)
        upstream.append(raw.elapsed_s - raw_before)
        non_matrix.append(max(0.0, total - travel.elapsed_s))
        provider_calls += raw.calls - calls_before
        hits += cached.hits - hits_before
        misses += cached.misses - misses_before
        fingerprints.append(fingerprint(asdict(plan)))
    required = sum(abs(j.delta) for j in case.jobs if j.station_id not in case.completed)
    report = {"elapsed_s": elapsed, "median_s": median(elapsed), "p95_s": summarize(elapsed)["p95_s"],
            "max_s": max(elapsed), "same_plan_each_run": len(set(fingerprints)) == 1,
            "plan_sha256": fingerprints[0], "warmup_s": warmup_s,
            "provider_calls": provider_calls, "cache_hits": hits, "cache_misses": misses,
            "timings": {"matrix": summarize(matrix), "provider": summarize(upstream),
                        "non_matrix": summarize(non_matrix)},
            "used_trucks": len(plan.routes), "required_bikes": required, "handled_bikes": plan.bikes_moved,
            "unserved_bikes": plan.unserved_bikes, "distance_m": plan.total_distance_m}
    if profile_stages:
        report["stages"] = {name: summarize(samples) for name, samples in stages.items()}
    return report


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    geography = cli.add_mutually_exclusive_group()
    geography.add_argument("--stations", type=int, default=200)
    geography.add_argument("--region", help="use all reference stations of this region; requires --all-candidates")
    cli.add_argument("--trucks", type=int, default=5)
    cli.add_argument("--seed", type=int, default=7)
    cli.add_argument("--repeats", type=int, default=3)
    cli.add_argument("--solution-limit", type=int, default=50)
    cli.add_argument("--all-candidates", action="store_true", help="stress input: every station requires service")
    cli.add_argument("--scenario", choices=("plan", "replan"), default="plan")
    cli.add_argument("--cache-mode", choices=("none", "cold", "warm", "compare"), default="none")
    cli.add_argument("--travel-source", choices=("haversine", "osrm"), default="haversine")
    cli.add_argument("--profile-stages", action="store_true", help="record prepare/model/search/extract samples")
    cli.add_argument("--osrm-url")
    cli.add_argument("--osrm-data-version")
    return cli


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    if min(args.stations, args.trucks, args.repeats, args.solution_limit) < 1:
        cli.error("stations, trucks, repeats and solution-limit must be positive")
    if args.region and not args.all_candidates:
        cli.error("--region requires --all-candidates: reference files contain no observed inventory or forecasts")
    if args.travel_source == "osrm":
        if not args.osrm_url or not args.osrm_data_version or args.osrm_data_version == "unversioned":
            cli.error("OSRM requires an explicit URL and road data version")
        try:
            provider = OsrmTravelTime(args.osrm_url, data_version=args.osrm_data_version)
        except ValueError as error:
            cli.error(str(error))
        namespace = provider.cache_namespace
    else:
        if args.osrm_url or args.osrm_data_version:
            cli.error("OSRM settings require --travel-source osrm")
        provider = HaversineTravelTime()
        namespace = "benchmark:haversine:30kph:v1"
    setup_started = perf_counter()
    try:
        case = build_case(args, provider)
    except (OSError, ValueError) as error:
        cli.error(str(error))
    setup_s = perf_counter() - setup_started
    report = {"measurement": "local engine including matrix preparation; excludes Kafka, backend and LLM",
              "measured_at": datetime.now(timezone.utc).isoformat(), "python_version": platform.python_version(),
              "platform": f"{platform.system()} {platform.machine()}", "ortools_version": ortools.__version__,
              "seed": None if args.region else args.seed, "input_stations": len(case.stations),
              "candidate_stations": len(case.jobs) - len(case.completed) - len(case.blocked),
              "trucks": len(case.trucks), "scenario": "all-candidates" if args.all_candidates else "mock-forecast",
              "operation": args.scenario.upper(), "solution_limit": args.solution_limit,
              "repeats": args.repeats, "p95_method": "nearest-rank", "travel_source": args.travel_source,
              "road_data_version": args.osrm_data_version, "cache_mode": args.cache_mode,
              "profile_stages": args.profile_stages,
              "input_sha256": case.fingerprint(), "setup_s": setup_s,
              "completed_stations": len(case.completed), "blocked_stations": len(case.blocked),
              "snapshot_elapsed_s": case.snapshot_elapsed_s,
              "positioned_trucks": sum(t.position is not None for t in case.trucks),
              "start_loads": [t.load for t in case.trucks]}
    if case.reference is not None:
        report["reference"] = case.reference
    modes = ("none", "cold", "warm") if args.cache_mode == "compare" else (args.cache_mode,)
    results = {mode: measure(case, provider, repeats=args.repeats, cache_mode=mode, namespace=namespace,
                             profile_stages=args.profile_stages) for mode in modes}
    same = all(r["same_plan_each_run"] for r in results.values()) and len({r["plan_sha256"] for r in results.values()}) == 1
    if args.cache_mode == "compare":
        report.update(cache_comparison=results, same_plan_across_cache_modes=same)
    else:
        report.update(results[args.cache_mode])
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    if not same:
        raise SystemExit("fixed-input repeatability check failed")
    return report


if __name__ == "__main__":
    main()
