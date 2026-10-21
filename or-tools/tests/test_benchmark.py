"""Benchmark reports must measure real work and preserve the compared plans."""
import json
from dataclasses import asdict, replace

import pytest

from rebalancing.benchmark import BenchmarkCase, build_case, main, measure, parser, summarize
from rebalancing.mock_data import DEPOT
from rebalancing.models import Imbalance, RoutingStation, Truck
from rebalancing.osrm import TravelTimeError
from rebalancing.problem import PlanningOptions
from rebalancing.travel_time import HaversineTravelTime
from rebalancing.planner import pywrapcp


@pytest.mark.parametrize("samples,expected", [(list(range(20, 0, -1)), 19), ([3, 1, 2], 3), ([7], 7)])
def test_p95_uses_nearest_rank_and_retains_samples(samples, expected):
    report = summarize(samples)
    assert report["p95_s"] == expected and report["samples_s"] == samples
    assert report["median_s"] <= report["p95_s"] <= report["max_s"]


def test_cache_comparison_preserves_results_and_excludes_warmup_calls():
    class Provider:
        calls = 0

        def matrix(self, points):
            self.calls += 1
            return HaversineTravelTime().matrix(points)

    case = BenchmarkCase([RoutingStation(1, "pickup", 37.5666, 126.9781),
                          RoutingStation(2, "dropoff", 37.5667, 126.9782)],
                         [Imbalance(1, 5), Imbalance(2, -5)], [Truck(0)], PlanningOptions())
    reports = {}
    for mode, actual_calls, hits, misses in [("none", 3, 0, 0), ("cold", 3, 0, 3), ("warm", 1, 3, 0)]:
        provider = Provider()
        report = measure(case, provider, repeats=3, cache_mode=mode, namespace="test")
        assert provider.calls == actual_calls
        assert report["provider_calls"] == (0 if mode == "warm" else 3)
        assert (report["cache_hits"], report["cache_misses"]) == (hits, misses)
        assert report["same_plan_each_run"] and report["handled_bikes"] == 10
        for total, matrix, other in zip(report["elapsed_s"], report["timings"]["matrix"]["samples_s"],
                                        report["timings"]["non_matrix"]["samples_s"]):
            assert total == pytest.approx(matrix + other)
        reports[mode] = report
    assert len({r["plan_sha256"] for r in reports.values()}) == 1
    assert reports["warm"]["timings"]["provider"]["samples_s"] == [0, 0, 0]


def test_replan_uses_completed_prefix_loads_positions_and_remaining_shift():
    args = parser().parse_args(["--stations", "30", "--trucks", "3", "--all-candidates"])
    original = build_case(args, HaversineTravelTime())
    args.scenario = "replan"
    case = build_case(args, HaversineTravelTime())
    assert case.completed and case.blocked and not case.completed & case.blocked
    assert any(t.load > 0 and t.position != DEPOT for t in case.trucks)
    assert {t.available_time_s for t in case.trucks} == {10800 - case.snapshot_elapsed_s}
    assert sum(s.bikes for s in original.stations) == sum(s.bikes for s in case.stations) + sum(t.load for t in case.trucks)
    plan = case.solve(HaversineTravelTime())
    visited = {s.station_id for r in plan.routes for s in r.stops}
    assert not visited & (case.completed | case.blocked)
    assert not {s.station_id for s in plan.unserved} & case.completed
    assert all(plan.unserved_reasons[sid] == "blocked" for sid in case.blocked)
    assert case.fingerprint() != original.fingerprint()


def test_original_cli_keeps_flat_output_and_adds_p95(capsys):
    report = main(["--stations", "10", "--trucks", "2", "--repeats", "2", "--all-candidates"])
    assert json.loads(capsys.readouterr().out) == report
    assert report["operation"] == "PLAN" and report["cache_mode"] == "none"
    assert {"elapsed_s", "median_s", "max_s", "plan_sha256", "handled_bikes", "distance_m", "p95_s"} <= report.keys()
    assert report["profile_stages"] is False and "stages" not in report


def test_stage_profile_does_not_change_plan_and_stays_inside_call(capsys):
    args = ["--stations", "30", "--trucks", "3", "--repeats", "2", "--all-candidates",
            "--scenario", "replan", "--cache-mode", "compare"]
    original = main(args)
    report = main([*args, "--profile-stages"])
    capsys.readouterr()
    assert report["profile_stages"] is True and report["same_plan_across_cache_modes"]
    assert original["input_sha256"] == report["input_sha256"]
    for mode in ("none", "cold", "warm"):
        measured = report["cache_comparison"][mode]
        assert measured["plan_sha256"] == original["cache_comparison"][mode]["plan_sha256"]
        assert set(measured["stages"]) == {"prepare_problem", "build_model", "search", "extract_plan"}
        for i, total in enumerate(measured["elapsed_s"]):
            accounted = sum(row["samples_s"][i] for row in measured["stages"].values())
            assert 0 <= accounted <= total
        for stage in measured["stages"].values():
            assert len(stage["samples_s"]) == 2
            assert 0 <= stage["median_s"] <= stage["p95_s"] <= stage["max_s"]


@pytest.mark.parametrize("seed", [1, 7, 19])
@pytest.mark.parametrize("scenario", ["plan", "replan"])
@pytest.mark.parametrize("solution_limit", [1, 50])
def test_native_evaluators_preserve_callback_plans(monkeypatch, seed, scenario, solution_limit):
    args = parser().parse_args(["--stations", "30", "--trucks", "3", "--all-candidates",
                               "--seed", str(seed), "--scenario", scenario,
                               "--solution-limit", str(solution_limit)])
    case = build_case(args, HaversineTravelTime())
    if seed == 19:
        eligible = [j.station_id for j in case.jobs if j.station_id not in case.completed | case.blocked]
        case.trucks[0] = replace(case.trucks[0], fixed_station_ids=tuple(eligible[:2]))
        case.trucks[1] = replace(case.trucks[1], capacity=10)
    expected = asdict(case.solve(HaversineTravelTime()))
    # Compare against Python callbacks in the same pinned OR-Tools runtime,
    # including the old unknown-sign registration. No host-specific golden plans.
    original_manager = pywrapcp.RoutingIndexManager
    managers = []

    def manager(*args):
        result = original_manager(*args)
        managers.append(result)
        return result

    def matrix(routing, values):
        index = managers[-1]
        return routing.RegisterTransitCallback(lambda a, b: values[index.IndexToNode(a)][index.IndexToNode(b)])

    def vector(routing, values):
        index = managers[-1]
        return routing.RegisterUnaryTransitCallback(lambda a: values[index.IndexToNode(a)])

    monkeypatch.setattr(pywrapcp, "RoutingIndexManager", manager)
    monkeypatch.setattr(pywrapcp.RoutingModel, "RegisterTransitMatrix", matrix)
    monkeypatch.setattr(pywrapcp.RoutingModel, "RegisterUnaryTransitVector", vector)
    assert asdict(case.solve(HaversineTravelTime())) == expected


def test_replan_cli_compares_same_snapshot_and_plans_in_all_cache_modes(capsys):
    report = main(["--stations", "30", "--trucks", "3", "--repeats", "2", "--all-candidates",
                   "--scenario", "replan", "--cache-mode", "compare"])
    assert json.loads(capsys.readouterr().out) == report
    assert report["operation"] == "REPLAN" and report["same_plan_across_cache_modes"]
    assert report["completed_stations"] > 0 and report["candidate_stations"] > 0
    assert set(report["cache_comparison"]) == {"none", "cold", "warm"}
    assert report["cache_comparison"]["warm"]["cache_hits"] == 2


@pytest.mark.parametrize("args", [["--stations", "0"], ["--trucks", "0"], ["--repeats", "0"],
                                 ["--solution-limit", "0"], ["--travel-source", "osrm"],
                                 ["--osrm-url", "http://127.0.0.1:5000"]])
def test_invalid_benchmark_settings_are_rejected(args):
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2


def test_provider_failure_is_not_replaced_by_an_estimate():
    class Unavailable:
        def matrix(self, points):
            raise TravelTimeError("timeout", "test timeout", retryable=True)

    args = parser().parse_args(["--stations", "10", "--all-candidates"])
    case = build_case(args, HaversineTravelTime())
    with pytest.raises(TravelTimeError, match="test timeout"):
        measure(case, Unavailable(), repeats=1, cache_mode="warm", namespace="test")


@pytest.mark.parametrize("scenario", ["plan", "replan"])
def test_reference_n01_uses_every_station_and_correct_depot_with_synthetic_state(scenario):
    from rebalancing.seeds import REFERENCE_REGIONS, REFERENCE_STATIONS, load_region_seed
    args = parser().parse_args(["--region", "N01", "--all-candidates", "--scenario", scenario,
                               "--solution-limit", "1"])
    case = build_case(args, HaversineTravelTime())
    seed = load_region_seed(REFERENCE_REGIONS, REFERENCE_STATIONS, "N01")
    assert len(case.stations) == len(case.jobs) == 265
    assert case.depot == seed.depot != DEPOT
    assert {s.id: (s.name, s.lat, s.lon, s.docks) for s in case.stations} == {
        int(s.data_id): (s.name, *s.position, s.docks) for s in seed.stations}
    assert case.reference["region_id"] == "N01" and case.reference["wire_id_field"] == "api_station_id"
    assert case.reference["inventory"].startswith("synthetic")
    assert all(0 <= s.bikes <= s.docks for s in case.stations)
    assert sum(s.bikes for s in case.stations) + sum(t.load for t in case.trucks) == 133 * 5
    if scenario == "replan":
        assert case.completed and case.blocked
        assert all(t.position is not None and t.available_time_s == 10800 - case.snapshot_elapsed_s for t in case.trucks)
        result = case.solve(HaversineTravelTime())
        assert not {s.station_id for r in result.routes for s in r.stops} & (case.completed | case.blocked)


def test_reference_cli_retains_provenance_and_reproducibility_across_cache_modes(capsys):
    report = main(["--region", "S09", "--all-candidates", "--repeats", "2", "--solution-limit", "1",
                   "--cache-mode", "compare"])
    assert json.loads(capsys.readouterr().out) == report
    assert report["input_stations"] == 56 and report["seed"] is None
    assert report["same_plan_across_cache_modes"]
    assert report["reference"]["depot_basis"].startswith("station-coordinate-mean")
    assert all(len(report["reference"][key]) == 64 for key in ("id_map_sha256", "regions_sha256", "stations_sha256"))


@pytest.mark.parametrize("args", [["--region", "N01"], ["--region", "N99", "--all-candidates"],
                                  ["--region", "N01", "--stations", "200", "--all-candidates"]])
def test_reference_inputs_reject_missing_workload_unknown_region_or_truncation(args):
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2



@pytest.mark.parametrize("region,count", [("N01", 2), ("N04", 2), ("S05", 3), ("S06", 4), ("S09", 1)])
def test_region_default_fleet_uses_reference_lower_bound_and_allows_explicit_override(region, count):
    args = parser().parse_args(["--region", region, "--all-candidates", "--solution-limit", "1"])
    case = build_case(args, HaversineTravelTime())
    assert len(case.trucks) == count
    assert case.reference["trucks_basis"] == "regional trucks_min reference default"
    args.trucks = 5
    case = build_case(args, HaversineTravelTime())
    assert len(case.trucks) == 5 and case.reference["trucks_basis"] == "explicit benchmark count"


def test_mock_default_fleet_and_travel_provenance_are_explicit(capsys):
    base = ["--stations", "10", "--all-candidates", "--solution-limit", "1", "--repeats", "1"]
    report = main(base)
    legacy = main([*base, "--speed-kph", "30", "--detour-factor", "1"])
    capsys.readouterr()
    assert report["trucks"] == 5
    assert report["travel_settings"] == {"speed_kph": 20.0, "detour_factor": 1.3}
    assert report["travel_namespace"] != legacy["travel_namespace"]
    assert report["input_sha256"] != legacy["input_sha256"]
