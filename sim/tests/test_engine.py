# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: 재생 규칙(실패 대여와 그 반납 지우기, 빈 대여소-분 적분)과 트럭 실행(재고 반영, 옮긴 대수)을 작은 가짜 하루로 확인한다.

import random
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from sim.engine import Day, replay, resample_demand
from sim.latent import poisson_arrivals
from sim.policies import Agent, CurrentOps, CurrentOpsLastWeek, NoRebalancing, Policy, SimpleRule
from forecast import Forecast
from rebalancing.models import Plan, Route, Station, Stop, Truck

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
        # 트럭은 거점에서 반쯤 싣고 나간다(depot_trucks). 용량 30이면 15대로 출발해 A에 13대를 내리고 B에서 회수할 수 있다
        plan = Agent(Fake(), frozenset({2, 3}), z=0).plan(at(7), stations, (37.501, 127.001), 1, 30)
        stops = {s.station_id: (s.action, s.bikes) for r in plan.routes for s in r.stops}
        self.assertEqual(stops.get(1), ("dropoff", 13))        # 최저 1 − 12 = −11 → 2대까지 13대
        self.assertEqual(stops.get(2, ("pickup",))[0], "pickup")
        self.assertNotIn(3, stops)
        self.assertNotIn(4, stops)

    def test_agent_picks_sure_overflow_outside_sources_only_when_supply_is_short(self):
        # A는 비어 가고(배치 14대), B는 공급처가 아니지만 반납만 들어와 확실히 넘친다(30 → 39, 거치대 10).
        # 빈 트럭이면 공급(0)이 배치 요청의 30%보다 적어 B에서 뺀다. 15대를 실은 트럭이면 B에 가지 않는다
        class Fake:
            demand = {1: (4.0, 0.0), 2: (0.0, 3.0)}

            def predict(self, st, start, horizon_h):
                rent, ret = self.demand[st.id]
                return Forecast(start, (rent,) * horizon_h, (ret,) * horizon_h, "fake")

        stations = [Station(1, "A", 37.500, 127.000, 10, 0), Station(2, "B", 37.501, 127.001, 10, 30)]
        agent = Agent(Fake(), frozenset(), z=0, pick_margin=5, pick_starve=0.3)
        stops = lambda load: {s.station_id: (s.action, s.bikes) for r in agent.plan(  # noqa: E731
            at(7), stations, (37.5005, 127.0005), [Truck(0, 30, load=load)], 30).routes for s in r.stops}
        self.assertEqual(stops(0), {1: ("dropoff", 14), 2: ("pickup", 28)})  # B: min(빼도 버티는 28, 확실한 넘침 39 − 10)
        self.assertEqual(stops(15), {1: ("dropoff", 14)})

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

    def test_policy_shifts_set_when_trucks_work(self):
        # 엔진은 정책의 shifts로 계획 시각을 잡는다. 없으면 기본 근무조(7–10, 13–16, 19–21)
        class Seen(SimpleRule):
            replan_h = 1

            def plan(self, now, *a, **k):
                self.at.append(now.hour)
                return super().plan(now, *a, **k)

        stations = {1: Station(1, "A", 37.500, 127.0, 10, 30), 2: Station(2, "B", 37.501, 127.0, 10, 3)}
        trips = [(at(10, 30 + 5 * i), 2, None, None) for i in range(3)]
        default, full = Seen(frozenset({1})), Seen(frozenset({1}))
        default.at, full.at, full.shifts = [], [], ((7, 22),)
        for p in (default, full):
            replay(Day(T0, stations, trips, (37.500, 127.0)), p, trucks=1)
        self.assertEqual(default.at, [7, 8, 9, 13, 14, 15, 19, 20])
        self.assertEqual(full.at, list(range(7, 22)))

    def test_carry_trucks_keeps_position_and_load_between_plans(self):
        # 07시: 거점에서 반쯤(7대) 싣고 나가 A에서 5대 회수. 08시 계획에는 트럭이 A에 12대를 싣고 있다고 넘어온다
        class Carry(Policy):
            replan_h, carry_trucks, shifts = 1, True, ((7, 9),)

            def __init__(self):
                self.seen = []

            def plan(self, now, stations, depot, trucks, cap, blocked=frozenset()):
                self.seen.append([(t.load, t.position, t.available_time_s) for t in trucks])
                if now.hour == 7:
                    return Plan([Route(0, trucks[0].load, [Stop(1, "pickup", 5, trucks[0].load + 5)])], [], "test")
                return Plan([], [], "test")

        stations = {1: Station(1, "A", 37.505, 127.0, 10, 20), 2: Station(2, "B", 37.500, 127.0, 10, 0)}
        p = Carry()
        r = replay(Day(T0, stations, [], (37.500, 127.0)), p, trucks=1, truck_capacity=14)
        self.assertEqual(p.seen[0], [(7, None, 2 * 3600)])
        self.assertEqual(p.seen[1], [(12, (37.505, 127.0), 3600)])
        self.assertEqual((r.stock[1], r.moved), (15, 5))

    def test_closed_station_blocks_rentals_diverts_returns_and_skips_trucks(self):
        # A는 08~10시 닫힘(대여·반납). 08:30 A 대여는 closed_rentals(그 반납도 없음), 09시 B→A 반납은 가장 가까운 열린 C로.
        # 08시 계획: 단순 규칙은 빈 A(재고 0)를 채우려 하지만 blocked로 받아 A에 가지 않는다
        stations = {1: Station(1, "A", 37.500, 127.000, 10, 0), 2: Station(2, "B", 37.520, 127.000, 10, 5),
                    3: Station(3, "C", 37.501, 127.000, 10, 0)}
        trips = [(at(8, 30), 1, at(8, 50), 2), (at(9), 2, at(9, 10), 1)]
        seen = []

        class Watch(SimpleRule):
            def plan(self, now, *a, blocked=frozenset(), **k):
                p = super().plan(now, *a, blocked=blocked, **k)
                seen.append((now.hour, blocked, {s.station_id for r in p.routes for s in r.stops}))
                return p

        day = Day(T0, stations, trips, (37.510, 127.0), closures={1: [(at(8), at(10), "both")]})
        r = replay(day, Watch(frozenset()), trucks=1)
        self.assertEqual((r.closed_rentals, r.failed, r.diverted_returns), (1, 0, 1))
        self.assertEqual(r.stock[2], 4)  # B: 09시 대여 하나만 나가고, A에서 빌리지 못한 자전거는 오지 않는다
        self.assertEqual(seen[0][:2], (7, frozenset({1})))  # 07~10시 근무조 계획이 도는 동안 A가 닫힌다
        self.assertNotIn(1, seen[0][2])  # 그래서 비어 있는 A를 채우러 가지 않는다
        self.assertEqual(seen[1][1], frozenset())  # 13시에는 다시 열려 있다

    def test_truck_skips_stop_at_closed_station(self):
        # 고정 회수 A(재고 22)에서 빼는 경로를 짰지만 A가 07시부터 닫혀 트럭이 건너뛴다
        stations = {1: Station(1, "A", 37.500, 127.000, 10, 22), 2: Station(2, "B", 37.501, 127.001, 10, 0)}

        class Blind(SimpleRule):  # 닫힘을 모르는 정책: 엔진이 정류장에서 막아야 한다
            def plan(self, now, *a, blocked=frozenset(), **k):
                return super().plan(now, *a, **k)

        day = Day(T0, stations, [], (37.5005, 127.0005), closures={1: [(at(7), at(12), "both")]})
        r = replay(day, Blind(frozenset({1})), trucks=1)
        self.assertEqual(r.stock[1], 22)
        self.assertGreater(r.skipped_stops, 0)

    def test_load_closures_reads_names_numbers_and_venue_defaults(self):
        import tempfile
        from datetime import date
        from sim.engine import load_closures
        stations = {1: Station(1, "여의나루역 1번출구 앞", 37.5, 127.0, 10, 0), 2: Station(2, "B", 37.5, 127.0, 10, 0),
                    3: Station(3, "C", 37.5, 127.0, 10, 0)}
        rows = ["date,start,end,scope,area,stations,station_count,reason,source",
                "2025-06-11,13:00,23:00,대여·반납 중단,여의도,여의나루역 1번출구앞;2;없는곳,3,불꽃,x",
                "2025-06-11,,,반납 중단,상암,,,행사,x",
                "2025-06-12,,,대여 중단,서울 전체,,,다른 날,x"]
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")
        with self.assertWarns(UserWarning):
            cl = load_closures(Path(f.name), date(2025, 6, 11), stations, {"여의나루역1번출구앞": 1, "B": 2}, {"상암": [3, 99]})
        self.assertEqual(cl[1], [(at(13), at(23), "both")])
        self.assertEqual(cl[2], [(at(13), at(23), "both")])
        self.assertEqual(cl[3], [(at(0), at(24), "return")])

    def test_load_closures_adds_venue_stations_when_count_exceeds_named(self):
        import tempfile
        from datetime import date
        from sim.engine import load_closures
        stations = {1: Station(1, "A", 37.5, 127.0, 10, 0), 2: Station(2, "B", 37.5, 127.0, 10, 0)}
        rows = ["date,start,end,scope,area,stations,station_count,reason,source",
                "2025-06-11,08:00,23:59,대여소 폐쇄,여의도,A,3,불꽃,x",
                "2025-06-11,19:00,,대여·반납 중단,여의도,,,불꽃,x"]  # 같은 대여소에 두 줄이 겹친다
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")
        with self.assertWarns(UserWarning):  # 1 + 700m 안 1곳 = 2곳, 공지는 3곳
            cl = load_closures(Path(f.name), date(2025, 6, 11), stations, {"A": 1}, {"여의도": [2]})
        self.assertEqual(cl[1], [(at(8), at(23, 59), "both")])
        self.assertEqual(cl[2], [(at(8), at(23, 59), "both"), (at(19), at(24), "both")])

    def test_load_closures_takes_nearest_stations_when_only_count_given(self):
        import tempfile
        from datetime import date
        from sim.engine import load_closures
        stations = {s: Station(s, str(s), 37.5, 127.0, 10, 0) for s in (4, 5, 6)}
        rows = ["date,start,end,scope,area,stations,station_count,reason,source",
                "2025-06-11,12:00,23:59,대여소 폐쇄,보신각,,2,타종,x"]
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")
        cl = load_closures(Path(f.name), date(2025, 6, 11), stations, {}, {"보신각": [5, 4, 6]})  # 가까운 순
        self.assertEqual(sorted(cl), [4, 5])

    def test_agent_passes_closed_stations_to_optimizer(self):
        class Fake:
            def predict(self, st, start, horizon_h):
                return Forecast(start, (4.0,) * horizon_h, (0.0,) * horizon_h, "fake")

        stations = [Station(1, "A", 37.500, 127.000, 10, 1), Station(2, "B", 37.501, 127.001, 10, 1)]
        plan = Agent(Fake(), frozenset(), z=0).plan(at(7), stations, (37.5005, 127.0005), 1, 30, blocked=frozenset({1}))
        stops = {s.station_id for r in plan.routes for s in r.stops}
        self.assertNotIn(1, stops)
        self.assertIn(1, {u.station_id for u in plan.unserved})

    def test_fail_priority_skips_empty_station_where_no_rentals_come(self):
        # A: 비었지만 3시간 동안 대여 예측이 0(LOW만큼 2대 모자람). B: 재고 1, 시간당 4대씩 빠진다(13대 모자람).
        # 트럭은 15대로 나가 둘 다 채울 수 있다. 기본 점수(대수 × 가중치)는 둘 다 채우고, 실패 기대값은 막을 실패가 없는 A를 건너뛴다
        class Fake:
            demand = {1: 0.0, 2: 4.0}

            def predict(self, st, start, horizon_h):
                return Forecast(start, (self.demand[st.id],) * horizon_h, (0.0,) * horizon_h, "fake")

        stations = [Station(1, "A", 37.505, 127.000, 10, 0), Station(2, "B", 37.495, 127.000, 10, 1)]
        stops = lambda p: {s.station_id: s.bikes for r in p.routes for s in r.stops}  # noqa: E731
        default = Agent(Fake(), frozenset(), z=0).plan(at(7), stations, (37.500, 127.000), 1, 30)
        fail = Agent(Fake(), frozenset(), z=0, fail_priority=0.25).plan(at(7), stations, (37.500, 127.000), 1, 30)
        self.assertEqual(stops(default), {1: 2, 2: 13})
        self.assertEqual(stops(fail), {2: 13})

    def test_resampled_day_keeps_hour_and_level_and_leaves_latent_alone(self):
        stations = {1: Station(1, "A", 37.50, 127.00, 10, 50), 2: Station(2, "B", 37.51, 127.01, 10, 0)}
        rec = [(at(8, m % 60), 1, at(8, m % 60) + timedelta(minutes=12), 2) for m in range(100)]  # 08시에 A → B 100건
        inflow = [(None, None, at(9), 1)]  # 전날·구역 밖에서 빌려 A에 반납
        latent = [(at(10), 2, None, None)]
        day = Day(T0, stations, rec + inflow + latent, (37.505, 127.005), n_recorded=101)
        a, b = resample_demand(day, seed=1), resample_demand(day, seed=1)
        self.assertEqual(a.trips, b.trips)  # 같은 시드면 같은 하루
        new = [t for t in a.trips[:a.n_recorded] if t[0] is not None]
        self.assertTrue(60 <= len(new) <= 140)  # 평균 100의 포아송
        self.assertTrue(all(t[0].hour == 8 and t[2] - t[0] == timedelta(minutes=12) and t[3] == 2 for t in new))
        self.assertIn(inflow[0], a.trips[:a.n_recorded])
        self.assertEqual(a.trips[a.n_recorded:], latent)

    def test_last_week_ops_replays_last_week_visits(self):
        stations = {1: Station(1, "A", 37.50, 127.00, 10, 0)}
        day = Day(T0, stations, [], (37.5, 127.0), visits=[(at(8), 1, 5)], visits_prev_week=[(at(9), 1, 3)])
        self.assertEqual(replay(day, CurrentOpsLastWeek()).stock, {1: 3})


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


DATA = Path(__file__).resolve().parents[2] / "data/shared/강북-04/rebal_visits.parquet"


@unittest.skipUnless(DATA.exists(), "구역 자료(data/shared)가 있을 때만")
class RecentSourcesTest(unittest.TestCase):
    def test_recent_sources_use_only_visits_that_ended_before_the_day(self):
        import duckdb
        from sim.engine import load_day
        t0 = datetime(2025, 6, 11)
        v = duckdb.sql(f"SELECT st, kind, n, lo, hi FROM '{DATA}'").df()
        w = v[(v.lo >= t0 - timedelta(weeks=8)) & (v.hi < t0)]
        net = w.assign(x=w.n.where(w.kind == "pick", -w.n)).groupby("st").x.sum() / 56
        day = load_day("강북-04", t0.date(), latent=False, sources_weeks=8)
        self.assertEqual(day.pickup_sources, frozenset(net[net >= 2].index) & day.stations.keys())
