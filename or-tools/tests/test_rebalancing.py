# Author: Jung Minkyu
# Date: 20OCT2026
# Purpose: Checks plan feasibility (load limits, no duplicate visits) and OR-Tools vs greedy cost.

import unittest
from datetime import datetime

from rebalancing.baseline import plan_greedy
from rebalancing.forecast import HeuristicPredictor
from rebalancing.imbalance import compute_imbalances, healthy_band
from rebalancing.mock_data import DEPOT, make_stations, make_trucks
from rebalancing.models import Station
from rebalancing.planner import plan_routes


def build(hour: int, seed: int = 7, trucks: int = 3):
    stations, kinds = make_stations(seed=seed)
    imbalances = compute_imbalances(stations, HeuristicPredictor(kinds, seed=seed), datetime(2025, 5, 1, hour))
    return stations, imbalances, make_trucks(trucks)


class ForecastTest(unittest.TestCase):
    def test_predict_returns_hourly_values_and_sums(self):
        s = Station(0, "x", 0, 0, capacity=20, bikes=10)
        f = HeuristicPredictor({0: "residential"}).predict(s, datetime(2025, 5, 1, 6), horizon_h=3)
        self.assertEqual((len(f.rentals_h), len(f.returns_h)), (3, 3))
        self.assertEqual((f.rentals, f.returns), (sum(f.rentals_h), sum(f.returns_h)))
        self.assertGreater(f.rentals_h[1], f.rentals_h[0])  # 06시는 평소, 07시는 주거지 출근 대여가 몰림


class HealthyBandTest(unittest.TestCase):
    def test_band_keeps_station_between_empty_and_full(self):
        s = Station(0, "x", 0, 0, capacity=20, bikes=10)
        lo, hi = healthy_band(s, rentals=8, returns=2, safety=2)
        self.assertEqual((lo, hi), (8, 20))  # must hold >= 8 to survive net outflow of 6
        lo, hi = healthy_band(s, rentals=2, returns=8, safety=2)
        self.assertEqual((lo, hi), (0, 14))  # must leave room for net inflow of 6


class PlannerInvariantsTest(unittest.TestCase):
    def check_plan(self, plan, imbalances, trucks):
        cap = trucks[0].capacity
        served = [s.station_id for r in plan.routes for s in r.stops]
        self.assertEqual(len(served), len(set(served)), "station visited twice")
        self.assertCountEqual(
            served + [i.station_id for i in plan.unserved], [i.station_id for i in imbalances]
        )
        delta = {i.station_id: i.delta for i in imbalances}
        for r in plan.routes:
            load = r.start_load
            self.assertTrue(0 <= load <= cap)
            for s in r.stops:
                load += delta[s.station_id]
                self.assertTrue(0 <= load <= cap, f"load {load} out of range")
                self.assertEqual(load, s.load_after)

    def test_ortools_plans_are_feasible(self):
        for hour in (8, 13, 18):
            for seed in (1, 7, 42):
                _, imbalances, trucks = build(hour, seed)
                plan = plan_routes(DEPOT, make_stations(seed=seed)[0], imbalances, trucks, time_limit_s=2)
                self.check_plan(plan, imbalances, trucks)

    def test_greedy_plans_are_feasible(self):
        stations, imbalances, trucks = build(8)
        self.check_plan(plan_greedy(DEPOT, stations, imbalances, trucks), imbalances, trucks)

    def test_ortools_not_worse_than_greedy_when_all_served(self):
        stations, imbalances, trucks = build(8)
        greedy = plan_greedy(DEPOT, stations, imbalances, trucks)
        ortools = plan_routes(DEPOT, stations, imbalances, trucks, time_limit_s=3)
        self.assertEqual(greedy.unserved_bikes, 0)
        self.assertEqual(ortools.unserved_bikes, 0)
        self.assertLessEqual(ortools.total_distance_m, greedy.total_distance_m)

    def test_solution_limit_gives_same_plan_every_time(self):
        stations, imbalances, trucks = build(8)
        a, b = (plan_routes(DEPOT, stations, imbalances, trucks, time_limit_s=60, solution_limit=50) for _ in range(2))
        self.assertEqual([(r.truck_id, [s.station_id for s in r.stops]) for r in a.routes],
                         [(r.truck_id, [s.station_id for s in r.stops]) for r in b.routes])
        self.check_plan(a, imbalances, trucks)

    def test_no_imbalance_gives_empty_plan(self):
        stations, _, trucks = build(8)
        plan = plan_routes(DEPOT, stations, [], trucks)
        self.assertEqual((plan.routes, plan.unserved), ([], []))


if __name__ == "__main__":
    unittest.main()
