"""Small physical scenarios with fixed travel times, independent of external services."""
from dataclasses import asdict, replace
import copy
import unittest

from rebalancing.baseline import plan_greedy
from rebalancing.imbalance import healthy_band
from rebalancing.models import Imbalance, Station, Truck
from rebalancing.planner import plan_routes
from rebalancing.problem import PlanningOptions
from rebalancing.travel_time import HaversineTravelTime, TravelMatrix

DEPOT = (37.0, 127.0)
STATIONS = [Station(1, "pickup", 37.01, 127.0, 10, 10), Station(2, "dropoff", 37.02, 127.0, 10, 0)]
NO_SERVICE = PlanningOptions(base_service_s=0, per_bike_service_s=0)
POLICIES = (plan_routes, plan_greedy)


class ConstantTravel:
    def __init__(self, seconds=10, metres=100):
        self.seconds, self.metres = seconds, metres

    def matrix(self, points):
        return TravelMatrix(
            [[0 if a == b else self.metres for b in points] for a in points],
            [[0 if a == b else self.seconds for b in points] for a in points],
        )


class LineTravel:
    """Coordinate identity, not supplied list order, determines costs."""
    def matrix(self, points):
        dist = [[round(abs(a[0] - b[0]) * 10_000) for b in points] for a in points]
        return TravelMatrix(dist, [[value // 10 for value in row] for row in dist])


def served(plan):
    return [stop.station_id for route in plan.routes for stop in route.stops]


class PhysicalConstraintsTest(unittest.TestCase):
    def test_empty_truck_cannot_deliver_without_a_pickup(self):
        for policy in POLICIES:
            with self.subTest(policy=policy.__name__):
                plan = policy(DEPOT, STATIONS, [Imbalance(2, -4)], [Truck(0, load=0)], travel_time=ConstantTravel())
                self.assertEqual(served(plan), [])
                self.assertEqual(plan.unserved_bikes, 4)

    def test_loaded_truck_delivers_and_keeps_exact_load(self):
        for policy in POLICIES:
            plan = policy(DEPOT, STATIONS, [Imbalance(2, -4)], [Truck(0, load=4)], travel_time=ConstantTravel())
            self.assertEqual(served(plan), [2])
            self.assertEqual(plan.routes[0].start_load, 4)
            self.assertEqual(plan.routes[0].stops[0].load_after, 0)

    def test_pickup_must_precede_delivery_for_empty_truck(self):
        for policy in POLICIES:
            plan = policy(DEPOT, STATIONS, [Imbalance(2, -4), Imbalance(1, 4)], [Truck(0)],
                          travel_time=ConstantTravel())
            self.assertEqual(served(plan), [1, 2])
            self.assertEqual([s.load_after for s in plan.routes[0].stops], [4, 0])

    def test_service_and_return_trip_are_included_in_shift(self):
        # 10s drive + (180 + 4*30)s work + 10s return = exactly 320s.
        for policy in POLICIES:
            truck = Truck(0, load=4, available_time_s=320)
            plan = policy(DEPOT, STATIONS, [Imbalance(2, -4)], [truck], travel_time=ConstantTravel())
            route = plan.routes[0]
            self.assertEqual((route.stops[0].arrival_s, route.stops[0].departure_s, route.duration_s), (10, 310, 320))
            late = policy(DEPOT, STATIONS, [Imbalance(2, -4)], [replace(truck, available_time_s=319)],
                          travel_time=ConstantTravel())
            self.assertEqual(served(late), [])

    def test_current_position_changes_first_leg(self):
        for policy in POLICIES:
            at_station = policy(DEPOT, STATIONS, [Imbalance(1, 4)],
                                [Truck(0, position=(STATIONS[0].lat, STATIONS[0].lon))],
                                travel_time=LineTravel(), options=NO_SERVICE)
            at_depot = policy(DEPOT, STATIONS, [Imbalance(1, 4)], [Truck(0)],
                              travel_time=LineTravel(), options=NO_SERVICE)
            self.assertEqual(at_station.routes[0].stops[0].arrival_s, 0)
            self.assertEqual(at_station.total_distance_m, 100)
            self.assertEqual(at_depot.total_distance_m, 200)

    def test_replan_excludes_completed_and_blocks_inaccessible_station(self):
        for policy in POLICIES:
            plan = policy(DEPOT, STATIONS, [Imbalance(1, 4), Imbalance(2, -4)], [Truck(0, load=4)],
                          completed_station_ids=frozenset({1}), blocked_station_ids=frozenset({2}),
                          travel_time=ConstantTravel())
            self.assertEqual(served(plan), [])
            self.assertEqual([i.station_id for i in plan.unserved], [2])
            self.assertEqual(plan.unserved_reasons, {2: "blocked"})

    def test_heterogeneous_capacities_are_respected(self):
        for policy in POLICIES:
            trucks = [Truck(0, capacity=3), Truck(1, capacity=8)]
            plan = policy(DEPOT, STATIONS, [Imbalance(1, 7)], trucks, travel_time=ConstantTravel())
            self.assertEqual(served(plan), [1])
            self.assertEqual(plan.routes[0].truck_id, 1)
            self.assertEqual(plan.routes[0].stops[0].load_after, 7)

    def test_replan_does_not_replay_completed_pickup_into_current_load(self):
        for policy in POLICIES:
            # The completed pickup has already emptied the station snapshot.
            stations = [replace(STATIONS[0], bikes=0), STATIONS[1]]
            plan = policy(DEPOT, stations, [Imbalance(1, 4), Imbalance(2, -4)],
                          [Truck(0, load=4, position=(STATIONS[0].lat, STATIONS[0].lon))],
                          completed_station_ids=frozenset({1}), travel_time=ConstantTravel())
            self.assertEqual(served(plan), [2])
            self.assertEqual(plan.routes[0].start_load, 4)
            self.assertEqual(plan.routes[0].stops[0].load_after, 0)
            self.assertEqual(plan.unserved, [])

    def test_quantity_larger_than_every_truck_is_explicitly_unserved(self):
        for policy in POLICIES:
            stations = [replace(STATIONS[0], bikes=20), STATIONS[1]]
            plan = policy(DEPOT, stations, [Imbalance(1, 16)], [Truck(0)], travel_time=ConstantTravel())
            self.assertEqual(plan.unserved_reasons, {1: "capacity_exceeded"})
            self.assertEqual(plan.status, "partial")

    def test_unavailable_unused_truck_does_not_invalidate_other_route(self):
        trucks = [Truck(0, position=(38.0, 127.0), available_time_s=0), Truck(1)]
        plan = plan_routes(DEPOT, STATIONS, [Imbalance(1, 4)], trucks, travel_time=ConstantTravel())
        self.assertEqual(served(plan), [1])
        self.assertEqual(plan.routes[0].truck_id, 1)

    def test_zero_trucks_returns_unserved_instead_of_entering_solver(self):
        for policy in POLICIES:
            plan = policy(DEPOT, STATIONS, [Imbalance(1, 4)], [])
            self.assertEqual(plan.unserved_reasons, {1: "no_trucks"})

    def test_no_work_does_not_call_external_matrix_provider(self):
        class Unavailable:
            def matrix(self, points):
                raise AssertionError("no travel matrix should be requested")
        for policy in POLICIES:
            self.assertEqual(policy(DEPOT, STATIONS, [], [Truck(0)], travel_time=Unavailable()).status, "complete")
            policy(DEPOT, STATIONS, [Imbalance(1, 4)], [], travel_time=Unavailable())


class PriorityAndRepeatabilityTest(unittest.TestCase):
    def test_shortage_priority_wins_when_only_one_visit_fits(self):
        # Two visits require 30s. Both individual visits fit in the 20s shift.
        plan = plan_routes(DEPOT, STATIONS, [Imbalance(1, 4), Imbalance(2, -4)],
                           [Truck(0, capacity=8, load=4, available_time_s=20)],
                           travel_time=ConstantTravel(), options=NO_SERVICE)
        self.assertEqual(served(plan), [2])
        self.assertEqual(plan.unserved_reasons, {1: "not_selected"})

    def test_explicit_priority_can_override_default_type_weight(self):
        plan = plan_routes(DEPOT, STATIONS, [Imbalance(1, 4, priority=100), Imbalance(2, -4)],
                           [Truck(0, capacity=8, load=4, available_time_s=20)],
                           travel_time=ConstantTravel(), options=NO_SERVICE)
        self.assertEqual(served(plan), [1])

    def test_same_input_and_reordered_snapshots_produce_same_plan(self):
        args = (DEPOT, STATIONS, [Imbalance(1, 4), Imbalance(2, -4)], [Truck(1), Truck(0)])
        first = asdict(plan_routes(*args, travel_time=LineTravel()))
        for _ in range(3):
            self.assertEqual(asdict(plan_routes(*args, travel_time=LineTravel())), first)
        reordered = plan_routes(DEPOT, list(reversed(STATIONS)), list(reversed(args[2])), list(reversed(args[3])),
                                 travel_time=LineTravel())
        self.assertEqual(asdict(reordered), first)


class NativeMatrixRegressionTest(unittest.TestCase):
    def test_reused_directed_matrix_is_not_changed_and_keeps_physical_costs(self):
        class ReusedTravel:
            def __init__(self):
                # depot, delivery, and two dedicated truck start nodes.
                self.saved = TravelMatrix([[0, 100, 300, 400], [200, 0, 500, 600],
                                           [700, 50, 0, 800], [900, 70, 1000, 0]],
                                          [[0, 10, 30, 40], [20, 0, 50, 60],
                                           [70, 5, 0, 80], [90, 7, 100, 0]])

            def matrix(self, _points):
                return self.saved

        provider = ReusedTravel()
        saved = copy.deepcopy(provider.saved)
        trucks = [Truck(0, load=4, position=(37.01, 127), available_time_s=325),
                  Truck(1, position=(38, 127), available_time_s=0)]
        plans = [plan_routes(DEPOT, STATIONS, [Imbalance(2, -4)], trucks, travel_time=provider) for _ in range(2)]
        self.assertEqual(provider.saved, saved)
        self.assertEqual(asdict(plans[0]), asdict(plans[1]))
        route, = plans[0].routes
        self.assertEqual(route.truck_id, 0)
        self.assertEqual((route.distance_m, route.duration_s), (250, 325))
        self.assertEqual((route.stops[0].arrival_s, route.stops[0].departure_s), (5, 305))
        # The used truck still needs its 20-second return. The unused, remote
        # zero-budget truck must not take a mandatory start->depot trip.
        late = plan_routes(DEPOT, STATIONS, [Imbalance(2, -4)],
                           [replace(trucks[0], available_time_s=324), trucks[1]], travel_time=provider)
        self.assertEqual(served(late), [])

    def test_profile_is_separate_from_plan_and_resets_for_skipped_solver(self):
        timings = {"previous": 123}
        plan = plan_routes(DEPOT, STATIONS, [Imbalance(1, 4), Imbalance(2, -4)], [Truck(0)],
                           travel_time=ConstantTravel(), timings=timings)
        original = plan_routes(DEPOT, STATIONS, [Imbalance(1, 4), Imbalance(2, -4)], [Truck(0)],
                               travel_time=ConstantTravel())
        self.assertEqual(asdict(plan), asdict(original))
        self.assertEqual(set(timings), {"prepare_problem", "build_model", "search", "extract_plan"})
        self.assertTrue(all(value >= 0 for value in timings.values()))
        for jobs, trucks in [([], [Truck(0)]), ([Imbalance(1, 4)], [])]:
            skipped = plan_routes(DEPOT, STATIONS, jobs, trucks, timings=timings)
            self.assertEqual(skipped.routes, [])
            self.assertEqual((timings["build_model"], timings["search"]), (0, 0))


class InventoryTargetTest(unittest.TestCase):
    def test_operating_target_can_exceed_dock_count(self):
        station = Station(1, "over-docked", 37, 127, 10, 31, operating_limit=35)
        self.assertEqual(healthy_band(station, 0, 0, 2), (2, 35))
        self.assertEqual(healthy_band(station, 0, 0, 2, operating_limit=20), (2, 20))

    def test_large_outflow_target_is_not_clipped_to_docks(self):
        self.assertEqual(healthy_band(STATIONS[0], 30, 0, 2), (32, 40))


class InputValidationTest(unittest.TestCase):
    def test_rejects_invalid_inputs_before_solver(self):
        cases = [
            (STATIONS, [Imbalance(1, 0)], [Truck(0)]),
            (STATIONS, [Imbalance(999, 4)], [Truck(0)]),
            (STATIONS, [Imbalance(1, 4), Imbalance(1, 2)], [Truck(0)]),
            (STATIONS, [Imbalance(1, 4, priority=-1)], [Truck(0)]),
            (STATIONS, [Imbalance(1, 11)], [Truck(0)]),
            (STATIONS, [Imbalance(1, 4)], [Truck(0, capacity=0)]),
            (STATIONS, [Imbalance(1, 4)], [Truck(0, load=16)]),
            (STATIONS, [Imbalance(1, 4)], [Truck(0, available_time_s=-1)]),
            (STATIONS, [Imbalance(1, 4)], [Truck(0), Truck(0)]),
            (STATIONS + [STATIONS[0]], [Imbalance(1, 4)], [Truck(0)]),
            ([replace(STATIONS[0], lat=float("nan"))], [Imbalance(1, 4)], [Truck(0)]),
        ]
        for stations, imbalances, trucks in cases:
            for policy in POLICIES:
                with self.subTest(policy=policy.__name__, imbalances=imbalances, trucks=trucks):
                    with self.assertRaises(ValueError):
                        policy(DEPOT, stations, imbalances, trucks, travel_time=ConstantTravel())

    def test_rejects_bad_matrix_dimensions_and_unreachable_values(self):
        for matrix in (TravelMatrix([[0]], [[0]]),
                       TravelMatrix([[0, 0], None], [[0, 0], [0, 0]]),
                       TravelMatrix([[0, None], [0, 0]], [[0, 0], [0, 0]])):
            with self.assertRaises(ValueError):
                matrix.validate(2)

    def test_rejects_invalid_search_and_travel_settings(self):
        with self.assertRaises(ValueError):
            PlanningOptions(solution_limit=0)
        with self.assertRaises(ValueError):
            HaversineTravelTime(speed_kph=0).matrix([DEPOT])
        with self.assertRaises(ValueError):
            plan_routes(DEPOT, STATIONS, [Imbalance(1, 4)], [Truck(0)], time_limit_s=0)

    def test_uses_directed_travel_times(self):
        class Directed:
            def matrix(self, points):
                distance = [[0 if a == b else (100 if a[0] < b[0] else 200) for b in points] for a in points]
                return TravelMatrix(distance, [[v // 10 for v in row] for row in distance])
        for policy in POLICIES:
            plan = policy(DEPOT, STATIONS, [Imbalance(1, 4)], [Truck(0)],
                          travel_time=Directed(), options=NO_SERVICE)
            self.assertEqual(plan.routes[0].duration_s, 30)
            self.assertEqual(plan.total_distance_m, 300)


if __name__ == "__main__":
    unittest.main()
