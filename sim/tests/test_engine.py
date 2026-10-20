# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: 재생 규칙(실패 대여와 그 반납 지우기, 빈 대여소-분 적분)과 트럭 실행(재고 반영, 옮긴 대수)을 작은 가짜 하루로 확인한다.

import random
import unittest
from datetime import datetime, timedelta

from sim.engine import Day, replay
from sim.latent import poisson_arrivals
from sim.policies import Agent, CurrentOps, NoRebalancing, SimpleRule
from rebalancing.models import Forecast, Station

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

    def test_latent_rentals_are_counted_apart_from_recorded(self):
        stations = {1: Station(1, "A", 37.50, 127.00, 10, 1)}
        trips = [(at(8), 1, None, None),       # 기록된 대여: 성공
                 (at(9), 1, None, None)]       # 억눌린 수요: A가 비어 실패
        r = replay(Day(T0, stations, trips, (37.5, 127.0), n_recorded=1), NoRebalancing())
        self.assertEqual((r.rentals, r.latent, r.failed, r.failed_latent), (2, 1, 1, 1))

    def test_agent_fills_station_that_will_empty_and_keeps_bikes_that_will_leave(self):
        # A: 재고 1, 3시간 동안 시간당 4대씩 빠질 예정 → 배치. B: 고정 회수 대여소, 재고 30(거치대 10), 반납이 계속 들어옴 → 회수.
        # C: 고정 회수 대여소지만 3시간 동안 시간당 9대씩 빠질 예정 → 빼면 부족하므로 회수하지 않는다. D: 넘치지만 고정 회수 대여소가 아니다
        class Fake:
            demand = {1: (4.0, 0.0), 2: (0.0, 3.0), 3: (9.0, 0.0), 4: (0.0, 3.0)}

            def predict(self, st, start, horizon_h):
                rent, ret = self.demand[st.id]
                return Forecast(start, (rent,) * horizon_h, (ret,) * horizon_h, "fake")

        stations = [Station(1, "A", 37.500, 127.000, 10, 1), Station(2, "B", 37.501, 127.001, 10, 30),
                    Station(3, "C", 37.502, 127.002, 10, 29), Station(4, "D", 37.503, 127.003, 10, 30)]
        plan = Agent(Fake(), frozenset({2, 3}), z=0, time_limit_s=1).plan(at(7), stations, (37.501, 127.001), 1, 15)
        stops = {s.station_id: (s.action, s.bikes) for r in plan.routes for s in r.stops}
        self.assertEqual(stops.get(1), ("dropoff", 13))        # 최저 1 − 12 = −11 → 2대까지 13대
        self.assertEqual(stops.get(2, ("pickup",))[0], "pickup")
        self.assertNotIn(3, stops)
        self.assertNotIn(4, stops)

    def test_hourly_replan_does_not_mix_loads_when_old_route_returns_on_the_hour(self):
        # 07시 경로: B 배치 → A 회수 → 멀리 있는 C에서 끊겨 08시 정각에 거점으로 돌아온다(귀환 처리).
        # D는 07시대 대여로 08시에 비어, 08시 새 경로가 D에 들른다. 예전에는 정각 귀환이 새 경로의 적재량을 지워 KeyError가 났다
        class Hourly(SimpleRule):
            replan_h = 1

        stations = {1: Station(1, "A", 37.500, 127.0, 10, 22), 2: Station(2, "B", 37.545, 127.0, 10, 0),
                    3: Station(3, "C", 37.700, 127.0, 10, 0), 4: Station(4, "D", 37.473, 127.0, 10, 5)}
        trips = [(at(7, 10 + 8 * i), 4, None, None) for i in range(5)]
        r = replay(Day(T0, stations, trips, (37.500, 127.0)), Hourly(frozenset({1})), trucks=1)
        self.assertEqual(r.stock[2], 5)   # 07시 경로가 B를 채웠다
        self.assertEqual(r.stock[1], 10)  # A에서 거치대 수까지 뺐다


class LatentTest(unittest.TestCase):
    def test_poisson_arrivals_stay_inside_and_follow_hourly_rate(self):
        s, e = at(7, 30), at(10)
        rate = lambda t: 0.0 if t.hour == 8 else 40.0  # noqa: E731  08시에는 아무도 오지 않는다
        ts = poisson_arrivals(s, e, rate, random.Random(0))
        self.assertTrue(all(s <= t < e for t in ts))
        self.assertFalse(any(t.hour == 8 for t in ts))
        self.assertTrue(40 <= len(ts) <= 80)  # 기댓값 0.5시간·40 + 1시간·40 = 60


if __name__ == "__main__":
    unittest.main()
