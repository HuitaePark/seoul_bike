# Author: Jung Minkyu
# Date: 20OCT2026
# Purpose: Checks plan feasibility (load limits, no duplicate visits) and OR-Tools vs greedy cost.

import unittest

from rebalancing.baseline import plan_greedy
from rebalancing.forecast import HeuristicPredictor
from rebalancing.imbalance import compute_imbalances, healthy_band
from rebalancing.mock_data import DEPOT, make_stations, make_trucks
from rebalancing.models import Station
from rebalancing.planner import plan_routes


def build(hour: int, seed: int = 7, trucks: int = 3):
    stations, kinds = make_stations(seed=seed)
    imbalances = compute_imbalances(stations, HeuristicPredictor(kinds, seed=seed), hour)
    return stations, imbalances, make_trucks(trucks)


class HealthyBandTest(unittest.TestCase):
    def test_band_targets_end_inventory_without_hard_dock_cap(self):
        s = Station(0, "x", 0, 0, capacity=20, bikes=10)
        lo, hi = healthy_band(s, rentals=8, returns=2, safety=2)
        self.assertEqual((lo, hi), (8, 26))  # can start above 20; net outflow brings it back to 20
        lo, hi = healthy_band(s, rentals=2, returns=8, safety=2)
        self.assertEqual((lo, hi), (0, 14))  # must leave room for net inflow of 6


class PlannerInvariantsTest(unittest.TestCase):
    def check_plan(self, plan, imbalances, trucks):
        by_id = {truck.id: truck for truck in trucks}
        served = [s.station_id for r in plan.routes for s in r.stops]
        self.assertEqual(len(served), len(set(served)), "station visited twice")
        self.assertCountEqual(
            served + [i.station_id for i in plan.unserved], [i.station_id for i in imbalances]
        )
        delta = {i.station_id: i.delta for i in imbalances}
        for r in plan.routes:
            truck = by_id[r.truck_id]
            cap = truck.capacity
            load = r.start_load
            self.assertEqual(load, truck.load)
            self.assertLessEqual(r.duration_s, truck.available_time_s)
            self.assertTrue(0 <= load <= cap)
            for s in r.stops:
                load += delta[s.station_id]
                self.assertTrue(0 <= load <= cap, f"load {load} out of range")
                self.assertEqual(load, s.load_after)

    def test_ortools_plans_are_feasible(self):
        for hour in (8, 13, 18):
            for seed in (1, 7, 42):
                _, imbalances, trucks = build(hour, seed)
                plan = plan_routes(DEPOT, make_stations(seed=seed)[0], imbalances, trucks)
                self.check_plan(plan, imbalances, trucks)

    def test_greedy_plans_are_feasible(self):
        stations, imbalances, trucks = build(8)
        self.check_plan(plan_greedy(DEPOT, stations, imbalances, trucks), imbalances, trucks)

    def test_ortools_not_worse_than_greedy_when_all_served(self):
        stations, imbalances, trucks = build(8)
        greedy = plan_greedy(DEPOT, stations, imbalances, trucks)
        ortools = plan_routes(DEPOT, stations, imbalances, trucks)
        self.assertEqual(greedy.unserved_bikes, 0)
        self.assertEqual(ortools.unserved_bikes, 0)
        self.assertLessEqual(ortools.total_distance_m, greedy.total_distance_m)

    def test_no_imbalance_gives_empty_plan(self):
        stations, _, trucks = build(8)
        plan = plan_routes(DEPOT, stations, [], trucks)
        self.assertEqual((plan.routes, plan.unserved), ([], []))


if __name__ == "__main__":
    unittest.main()
