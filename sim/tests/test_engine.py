# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: 재생 규칙(실패 대여와 그 반납 지우기, 빈 대여소-분 적분)과 트럭 실행(재고 반영, 옮긴 대수)을 작은 가짜 하루로 확인한다.

import unittest
from datetime import datetime, timedelta

from sim.engine import Day, replay
from sim.policies import CurrentOps, NoRebalancing, SimpleRule
from rebalancing.models import Station

T0 = datetime(2025, 6, 11)


def at(h, m=0):
    return T0 + timedelta(hours=h, minutes=m)


class ReplayTest(unittest.TestCase):
    def test_failed_rental_drops_its_return_and_empty_minutes_add_up(self):
        stations = {1: Station(1, "A", 37.50, 127.00, 10, 0), 2: Station(2, "B", 37.51, 127.01, 10, 1)}
        trips = [(at(1), 1, at(1, 10), 2),  # A가 비어 실패 → B 반납도 없음
                 (at(2), 2, at(2, 30), 1)]  # B에서 빌려 A에 반납
        r = replay(Day(T0, stations, trips, (37.505, 127.005)), NoRebalancing())
        self.assertEqual((r.rentals, r.failed), (2, 1))
        self.assertEqual(r.stock, {1: 1, 2: 0})
        self.assertAlmostEqual(r.empty_min, 150 + 22 * 60)  # A는 00:00~02:30, B는 02:00~24:00 비어 있음

    def test_simple_rule_truck_drops_then_picks_within_shift(self):
        # 고정 회수 대여소 A는 거치대보다 12대 많고 B는 비었다. 트럭은 반쯤 싣고 나가 B를 먼저 채우고 A에서 뺀다
        stations = {1: Station(1, "A", 37.500, 127.000, 10, 22), 2: Station(2, "B", 37.501, 127.001, 10, 0)}
        r = replay(Day(T0, stations, [], (37.5005, 127.0005)), SimpleRule(frozenset({1})), trucks=1)
        self.assertEqual(r.stock, {1: 10, 2: 5})
        self.assertEqual(r.moved, 12)
        self.assertGreater(r.truck_h, 0)

    def test_simple_rule_does_not_pick_from_non_fixed_station(self):
        stations = {1: Station(1, "A", 37.500, 127.000, 10, 22)}
        r = replay(Day(T0, stations, [], (37.5, 127.0)), SimpleRule(frozenset()), trucks=1)
        self.assertEqual((r.stock, r.moved), ({1: 22}, 0))

    def test_current_ops_replays_visits_and_counts_short_pickups(self):
        stations = {1: Station(1, "A", 37.50, 127.00, 10, 3), 2: Station(2, "B", 37.51, 127.01, 10, 0)}
        visits = [(at(8), 1, -5), (at(8, 30), 2, +4)]  # A에서 5대 회수하려 했지만 3대뿐
        r = replay(Day(T0, stations, [], (37.505, 127.005), visits=visits), CurrentOps())
        self.assertEqual(r.stock, {1: 0, 2: 4})
        self.assertEqual((r.moved, r.pick_short), (3, 2))


if __name__ == "__main__":
    unittest.main()
