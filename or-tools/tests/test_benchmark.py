"""Benchmark reports must measure real work and preserve the compared plans."""
import json

import pytest

from rebalancing.benchmark import BenchmarkCase, build_case, main, measure, parser, summarize
from rebalancing.mock_data import DEPOT
from rebalancing.models import Imbalance, RoutingStation, Truck
from rebalancing.osrm import TravelTimeError
from rebalancing.problem import PlanningOptions
from rebalancing.travel_time import HaversineTravelTime


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
