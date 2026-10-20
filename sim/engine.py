# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: 하루치 대여·반납 기록을 분 단위 순서대로 재고에 반영하고, 정책이 낸 트럭 경로를 실행해 지표를 센다.

"""재생 엔진 (M2, PRD 9.1).

재생 규칙 (docs/roles/06-data-sim.md 5절):
  - 재고는 대여소별 대수로만 센다(자전거 번호는 따라가지 않는다).
  - 초기 재고는 역산 재고 복원(analysis/rebal/04_stock.py)과 같은 방식으로 하루 시작 시각에 센다.
  - 재고가 0일 때 기록된 대여는 실패 대여로 세고, 그 대여의 반납도 지운다(자전거 수 보존).
  - 반납 대여소가 빈 대여는 대여·반납 모두 뺀다. 구역 밖 대여소의 재고는 재생하지 않는다(구역 밖에서 온 반납은 그대로 들어온다).
  - 거치대 수를 넘는 반납도 받는다. 거치대 수를 넘은 시간은 과적재 지표로만 센다(PRD 7.1).
트럭: 정책이 근무조 시작마다 rebalancing.models.Plan을 내면, 경로를 시간에 맞춰 실행한다.
      도착할 때 실제 재고·적재량에 맞춰 싣고 내린다. 근무조가 끝나면 남은 정류장은 버리고 거점으로 돌아간다.
"""
from __future__ import annotations

import heapq
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

from rebalancing.geo import haversine_m
from rebalancing.models import Plan, Station

ROOT = Path(__file__).resolve().parents[1]
SHIFTS = ((7, 10), (13, 16), (19, 21))  # 근무조 (PRD 7.4, 역산 추정)
SPEED_KMH = 20  # 시내 트럭 평균 속도 (초기값)
DETOUR = 1.3  # 직선거리 → 도로 거리 보정 (초기값. OSRM이 생기면 바꾼다)


@dataclass
class Day:
    """하루 재생에 필요한 입력. load_day가 파일에서 만들고, 시험에서는 직접 만든다."""

    start: datetime  # 하루 시작(00:00)
    stations: dict[int, Station]  # 구역 안에서 그날 운영한 대여소. Station.bikes = 초기 재고
    trips: list[tuple[datetime | None, int | None, datetime | None, int | None]]  # (대여 시각, 대여소, 반납 시각, 반납소). 구역 밖·하루 밖이면 None
    depot: tuple[float, float]  # 트럭 거점(구역 대여소의 중심)
    fixed_pickup: frozenset[int] = frozenset()  # 고정 회수 대여소 (stations.csv role, 단순 규칙이 쓴다)
    visits: list[tuple[datetime, int, int]] = field(default_factory=list)  # 역산 트럭 방문 (시각, 대여소, ±대수). 회수는 음수


@dataclass
class Result:
    rentals: int = 0  # 시도한 대여
    failed: int = 0  # 재고가 0이라 실패한 대여
    failed_at: Counter = field(default_factory=Counter)  # 대여소별 실패 대여
    empty_min: float = 0.0  # 빈 대여소-분 (주 지표)
    over_min: float = 0.0  # 과적재 대여소-분 (재고 > 거치대 수)
    moved: int = 0  # 트럭이 실제로 옮긴 대수(회수 기준)
    pick_short: int = 0  # 회수하러 갔는데 재고가 모자라 덜 실은 대수
    truck_h: float = 0.0  # 트럭 운행 시간
    stock: dict[int, int] = field(default_factory=dict)  # 하루가 끝났을 때 재고

    @property
    def moved_per_truck_h(self) -> float:
        return self.moved / self.truck_h if self.truck_h else 0.0


def leg_minutes(a: tuple[float, float], b: tuple[float, float]) -> float:
    return haversine_m(*a, *b) * DETOUR / (SPEED_KMH * 1000 / 60)


def stop_minutes(bikes: int) -> float:
    return 3 + 0.5 * bikes  # PRD 7.2 정차 시간 초기값


def replay(day: Day, policy, trucks: int = 3, truck_capacity: int = 15) -> Result:
    end = day.start + timedelta(days=1)
    stock = {s: st.bikes for s, st in day.stations.items()}
    hold = {s: st.capacity for s, st in day.stations.items()}
    since = {s: day.start for s in stock}  # 재고가 마지막으로 바뀐 시각(지표 적분용)
    res = Result()
    depot_bikes = trucks * truck_capacity // 2  # 거점 예비 자전거. 트럭이 반쯤 싣고 나가게(plan_greedy 가정)

    def change(s: int, now: datetime, dx: int) -> None:
        mins = (now - since[s]).total_seconds() / 60
        res.empty_min += mins * (stock[s] == 0)
        res.over_min += mins * (stock[s] > hold[s])
        stock[s] += dx
        since[s] = now

    # 사건 큐: (시각, 순서, 종류, 값). 같은 시각이면 대여(0) → 반납(1) → 트럭(2) 순
    q: list = []
    for i, (rt, rs, ut, us) in enumerate(day.trips):
        if rt is not None and rs in stock:
            q.append((rt, 0, "rent", i))
        if ut is not None and us in stock:
            q.append((ut, 1, "return", i))
    for h0, h1 in SHIFTS:
        q.append((day.start + timedelta(hours=h0), 2, "plan", day.start + timedelta(hours=h1)))
    ops = [(t, s, dx) for t, s, dx in policy.events(day) if s in stock and day.start <= t < end]
    q += [(t, 2, "ops", (s, dx)) for t, s, dx in ops]
    if ops:  # 경로가 없는 정책(현행 운영 재현)은 트럭이 근무조 내내 움직였다고 본다
        res.truck_h = trucks * sum(h1 - h0 for h0, h1 in SHIFTS)
    heapq.heapify(q)
    failed_trips: set[int] = set()
    load: dict[int, int] = {}  # 트럭별 적재량

    while q:
        now, _, kind, v = heapq.heappop(q)
        if now >= end:
            break
        if kind == "rent":
            s = day.trips[v][1]
            res.rentals += 1
            if stock[s] > 0:
                change(s, now, -1)
            else:
                res.failed += 1
                res.failed_at[s] += 1
                failed_trips.add(v)
        elif kind == "return":
            if v not in failed_trips:
                change(day.trips[v][3], now, +1)
        elif kind == "plan":
            snapshot = [Station(s, st.name, st.lat, st.lon, st.capacity, stock[s]) for s, st in day.stations.items()]
            plan: Plan = policy.plan(now, snapshot, day.depot, trucks, truck_capacity)
            for r in plan.routes:
                start_load = min(r.start_load, depot_bikes)
                depot_bikes -= start_load
                load[r.truck_id] = start_load
                t, pos = now, day.depot
                for stop in r.stops:
                    st = day.stations[stop.station_id]
                    t = t + timedelta(minutes=leg_minutes(pos, (st.lat, st.lon)))
                    if t >= v:  # 근무조가 끝나면 남은 정류장은 버린다
                        break
                    heapq.heappush(q, (t, 2, "stop", (r.truck_id, stop.station_id, stop.action, stop.bikes)))
                    t += timedelta(minutes=stop_minutes(stop.bikes))
                    pos = (st.lat, st.lon)
                back = min(t + timedelta(minutes=leg_minutes(pos, day.depot)), v)
                res.truck_h += (back - now).total_seconds() / 3600
                heapq.heappush(q, (back, 3, "unload", r.truck_id))
        elif kind == "stop":
            truck, s, action, bikes = v
            if action == "pickup":
                n = min(bikes, stock[s], truck_capacity - load[truck])
                load[truck] += n
                res.moved += n
                res.pick_short += bikes - n
                change(s, now, -n)
            else:
                n = min(bikes, load[truck])
                load[truck] -= n
                change(s, now, +n)
        elif kind == "unload":
            depot_bikes += load.pop(v)
        elif kind == "ops":
            s, dx = v
            n = max(dx, -stock[s])  # 회수는 있는 만큼만
            res.moved += -n if n < 0 else 0
            res.pick_short += n - dx
            change(s, now, n)

    for s in stock:  # 하루 끝까지 남은 시간을 지표에 더한다
        change(s, end, 0)
    res.stock = stock
    return res


def load_day(terr: str, d: date) -> Day:
    """구역 terr의 d일 하루를 파일에서 만든다.

    입력: analysis/out/stations.csv, data/shared/<구역>/trips.parquet (export_region.py),
          analysis/rebal/out/gaps_2025.parquet·bike_pick·bike_drop (초기 재고 복원. 2025년만 있다)
    """
    import duckdb  # 시험(test_engine)은 파일 없이 돌도록 여기서만 쓴다

    t0 = datetime.combine(d, time())
    t1 = t0 + timedelta(days=1)
    reb = ROOT / "analysis/rebal/out"
    c = duckdb.connect()
    c.execute(f"CREATE TABLE zs AS SELECT * FROM '{ROOT}/analysis/out/stations.csv' WHERE terr = ?", [terr])
    c.execute(f"""CREATE VIEW tr AS SELECT rent_dt, rent_st, rtn_dt, rtn_st FROM '{ROOT}/data/shared/{terr}/trips.parquet'
                  WHERE rtn_st IS NOT NULL""")
    # 초기 재고: 하루 시작 시각에 서 있는 자전거. 트럭이 옮긴 자전거는 회수 창 중간 전에는 원래 대여소, 배치 창 중간 뒤에는 새 대여소
    stock0 = dict(c.execute(f"""
    WITH g AS (SELECT * FROM '{reb}/gaps_2025.parquet' WHERE t_from <= $t0 AND t_to > $t0),
    p AS (SELECT bike_id, t_to, lo + (hi - lo) / 2 AS m FROM '{reb}/bike_pick.parquet'),
    d AS (SELECT bike_id, t_to, lo + (hi - lo) / 2 AS m FROM '{reb}/bike_drop.parquet')
    SELECT st, count(*)::INT FROM (
      SELECT from_st AS st FROM g WHERE NOT moved
      UNION ALL SELECT g.from_st FROM g JOIN p USING (bike_id, t_to) WHERE $t0 < p.m
      UNION ALL SELECT g.to_st FROM g JOIN d USING (bike_id, t_to) WHERE $t0 >= d.m)
    WHERE st IN (SELECT st_no FROM zs) GROUP BY 1""", {"t0": t0}).fetchall())
    # 그날 운영한 대여소: 앞뒤 사흘 안에 대여나 반납이 있었던 곳(폐쇄된 대여소를 빈 대여소로 세지 않게)
    rows = c.execute("""
    SELECT st_no, st_name, lat, lon, coalesce(hold_num, 0) FROM zs WHERE st_no IN (
      SELECT rent_st FROM tr WHERE rent_dt >= $a AND rent_dt < $b UNION SELECT rtn_st FROM tr WHERE rtn_dt >= $a AND rtn_dt < $b)
    ORDER BY st_no""", {"a": t0 - timedelta(days=3), "b": t1 + timedelta(days=3)}).fetchall()
    stations = {no: Station(no, name, lat, lon, cap, stock0.get(no, 0)) for no, name, lat, lon, cap in rows}
    trips = [(rt if t0 <= rt < t1 and rs in stations else None, rs if rs in stations else None,
              ut if t0 <= ut < t1 and us in stations else None, us if us in stations else None)
             for rt, rs, ut, us in c.execute("""SELECT rent_dt, rent_st, rtn_dt, rtn_st FROM tr
                 WHERE (rent_dt >= $a AND rent_dt < $b) OR (rtn_dt >= $a AND rtn_dt < $b) ORDER BY rent_dt""",
                                            {"a": t0, "b": t1}).fetchall()]
    lat = sum(s.lat for s in stations.values()) / len(stations)
    lon = sum(s.lon for s in stations.values()) / len(stations)
    fixed = frozenset(r[0] for r in c.execute("SELECT st_no FROM zs WHERE role = '고정 회수'").fetchall()) & stations.keys()
    # 역산 방문은 시각 창의 중간에 일어났다고 본다(초기 재고 복원과 같은 기준)
    visits = [(m, st, -n if kind == "pick" else n) for m, st, kind, n in c.execute(f"""
        SELECT lo + (hi - lo) / 2 AS m, st, kind, n::INT FROM '{ROOT}/data/shared/{terr}/rebal_visits.parquet'
        WHERE lo + (hi - lo) / 2 >= $a AND lo + (hi - lo) / 2 < $b ORDER BY m""", {"a": t0, "b": t1}).fetchall()
              if st in stations]
    return Day(t0, stations, trips, (lat, lon), fixed, visits)
