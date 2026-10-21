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
      정책에 carry_trucks가 있으면 근무조 안에서 다시 계획할 때 트럭을 거점으로 되돌리지 않고, 마지막으로 들른 곳·적재량·
      남은 근무 시간을 rebalancing.models.Truck으로 넘긴다(PRD v0.2 재경로). 거점에서 싣는 것은 근무조 시작 때 한 번이다.
대여소 일시 운영 중단(Day.closures, load_closures): 닫힌 동안의 대여는 실패 대여와 따로 closed_rentals로 세고 그 반납도 지운다.
      반납은 가장 가까운 열린 대여소로 옮긴다. 트럭은 닫힌 대여소에 도착하면 건너뛰고, 정책은 계획할 때 닫힌 대여소를 받는다(blocked).
"""
from __future__ import annotations

import csv
import heapq
import math
import warnings
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path

from rebalancing.geo import haversine_m
from rebalancing.models import Plan, Station, Truck
from rebalancing.travel_time import DEFAULT_DETOUR_FACTOR, DEFAULT_SPEED_KPH, HaversineTravelTime

ROOT = Path(__file__).resolve().parents[1]
SHIFTS = ((7, 10), (13, 16), (19, 21))  # 근무조 (PRD 7.4, 역산 추정)
SPEED_KMH = DEFAULT_SPEED_KPH  # 시내 트럭 평균 속도 (초기값)
DETOUR = DEFAULT_DETOUR_FACTOR  # 직선거리 → 도로 거리 보정 (초기값. OSRM이 생기면 바꾼다)


@dataclass
class Day:
    """하루 재생에 필요한 입력. load_day가 파일에서 만들고, 시험에서는 직접 만든다."""

    start: datetime  # 하루 시작(00:00)
    stations: dict[int, Station]  # 구역 안에서 그날 운영한 대여소. Station.bikes = 초기 재고
    trips: list[tuple[datetime | None, int | None, datetime | None, int | None]]  # (대여 시각, 대여소, 반납 시각, 반납소). 구역 밖·하루 밖이면 None
    depot: tuple[float, float]  # 트럭 거점(구역 대여소의 중심)
    fixed_pickup: frozenset[int] = frozenset()  # 고정 회수 대여소 (stations.csv role, 단순 규칙이 쓴다)
    pickup_sources: frozenset[int] = frozenset()  # 전해 4~10월 역산에서 트럭이 꾸준히 빼 간 대여소 (본 시스템의 공급처)
    visits: list[tuple[datetime, int, int]] = field(default_factory=list)  # 역산 트럭 방문 (시각, 대여소, ±대수). 회수는 음수
    visits_prev_week: list[tuple[datetime, int, int]] = field(default_factory=list)  # 지난주 같은 요일의 역산 방문을 이날로 옮긴 것
    n_recorded: int | None = None  # trips 중 앞의 n개가 기록된 대여, 나머지는 억눌린 수요(latent.py). None이면 모두 기록
    closures: dict[int, list[tuple[datetime, datetime, str]]] = field(default_factory=dict)  # 일시 운영 중단: 대여소 → [(시작, 끝, 'rent'·'return'·'both')]


@dataclass
class Result:
    rentals: int = 0  # 시도한 대여
    failed: int = 0  # 재고가 0이라 실패한 대여
    failed_at: Counter = field(default_factory=Counter)  # 대여소별 실패 대여
    failed_hour: Counter = field(default_factory=Counter)  # 시각(0~23)별 실패 대여
    latent: int = 0  # 시도한 대여 중 억눌린 수요
    failed_latent: int = 0  # 실패한 대여 중 억눌린 수요
    empty_min: float = 0.0  # 빈 대여소-분 (주 지표)
    over_min: float = 0.0  # 과적재 대여소-분 (재고 > 거치대 수)
    moved: int = 0  # 트럭이 실제로 옮긴 대수(회수 기준)
    pick_short: int = 0  # 회수하러 갔는데 재고가 모자라 덜 실은 대수
    dropped: int = 0  # 내린(배치한) 대수. moved는 회수만 센다
    truck_h: float = 0.0  # 트럭 운행 시간
    closed_rentals: int = 0  # 대여소가 닫혀 못 한 대여(판단 지표인 failed와 따로 센다)
    diverted_returns: int = 0  # 반납소가 닫혀 가장 가까운 열린 대여소로 옮긴 반납
    skipped_stops: int = 0  # 대여소가 닫혀 트럭이 건너뛴 정류장
    stock: dict[int, int] = field(default_factory=dict)  # 하루가 끝났을 때 재고

    @property
    def moved_per_truck_h(self) -> float:
        return self.moved / self.truck_h if self.truck_h else 0.0


def leg_minutes(a: tuple[float, float], b: tuple[float, float]) -> float:
    return haversine_m(*a, *b) * DETOUR / (SPEED_KMH * 1000 / 60)


def stop_minutes(bikes: int) -> float:
    return 3 + 0.5 * bikes  # PRD 7.2 정차 시간 초기값 (5의 PlanningOptions 기본값 180초 + 대당 30초와 같다)


class SimTravelTime:
    """Use the optimizer's shared estimate (agreement 19), matching leg_minutes.

    Road-adjusted integer distances now also use DETOUR, instead of reporting
    straight-line distances while budgeting road-adjusted travel time.
    """

    def matrix(self, points):
        return HaversineTravelTime(SPEED_KMH, DETOUR).matrix(points)


def replay(day: Day, policy, trucks: int = 3, truck_capacity: int = 15) -> Result:
    end = day.start + timedelta(days=1)
    stock = {s: st.bikes for s, st in day.stations.items()}
    hold = {s: st.docks for s, st in day.stations.items()}
    since = {s: day.start for s in stock}  # 재고가 마지막으로 바뀐 시각(지표 적분용)
    res = Result()
    depot_bikes = trucks * truck_capacity // 2  # 거점 예비 자전거. 트럭이 반쯤 싣고 나가게(plan_greedy 가정)

    def closed(s: int, now: datetime, what: str) -> bool:  # what: 'rent' 또는 'return'
        return any(a <= now < b and k in (what, "both") for a, b, k in day.closures.get(s, ()))

    near: dict[int, list[int]] = {}  # 닫힌 반납소 → 다른 대여소를 가까운 순으로

    def change(s: int, now: datetime, dx: int) -> None:
        mins = (now - since[s]).total_seconds() / 60
        res.empty_min += mins * (stock[s] == 0)
        res.over_min += mins * (stock[s] > hold[s])
        stock[s] += dx
        since[s] = now

    # 사건 큐: (시각, 순서, 종류, 값). 같은 시각이면 대여(0) → 반납(1) → 거점 귀환(1.5) → 트럭(2) 순. 귀환이 먼저라야 같은 시각의 계획이 돌아온 자전거를 싣는다
    q: list = []
    for i, (rt, rs, ut, us) in enumerate(day.trips):
        if rt is not None and rs in stock:
            q.append((rt, 0, "rent", i))
        if ut is not None and us in stock:
            q.append((ut, 1, "return", i))
    step = getattr(policy, "replan_h", None)  # 있으면 근무조 안에서 이 간격마다 다시 계획한다
    shifts = getattr(policy, "shifts", SHIFTS)  # 근무 시간을 바꿔 보는 시나리오(sim --shifts)
    for h0, h1 in shifts:
        for h in range(h0, h1, step or h1 - h0):
            q.append((day.start + timedelta(hours=h), 2, "plan", (day.start + timedelta(hours=min(h + (step or h1 - h0), h1)),
                                                                  day.start + timedelta(hours=h1))))
    carry = getattr(policy, "carry_trucks", False)
    same_supply = getattr(policy, "same_supply", False)  # 역산 방문 재생도 그날 회수한 것 + 거점 예비 안에서만 내린다
    fleet: dict[int, tuple] = {}  # carry일 때 근무조 안의 트럭 상태: 트럭 → (경로 키, 위치, 다음 일을 시작할 수 있는 시각)
    ops = [(t, s, dx) for t, s, dx in policy.events(day) if s in stock and day.start <= t < end]
    q += [(t, 1.9 if dx < 0 else 2, "ops", (s, dx)) for t, s, dx in ops]  # 같은 시각이면 회수가 먼저: 같은 공급 규칙에서 짝이 되는 배치가 잘리지 않게
    if ops:  # 경로가 없는 정책(현행 운영 재현)은 트럭이 근무조 내내 움직였다고 본다
        res.truck_h = trucks * sum(h1 - h0 for h0, h1 in SHIFTS)
    heapq.heapify(q)
    failed_trips: set[int] = set()
    load: dict[tuple, int] = {}  # 경로별 적재량. 키는 (트럭, 계획 시각): 다시 계획한 정각에 이전 경로의 귀환과 겹쳐도 섞이지 않게

    while q:
        now, _, kind, v = heapq.heappop(q)
        if now >= end:
            break
        if kind == "rent":
            s = day.trips[v][1]
            is_latent = day.n_recorded is not None and v >= day.n_recorded
            res.rentals += 1
            res.latent += is_latent
            if closed(s, now, "rent"):
                res.closed_rentals += 1
                failed_trips.add(v)
            elif stock[s] > 0:
                change(s, now, -1)
            else:
                res.failed += 1
                res.failed_latent += is_latent
                res.failed_at[s] += 1
                res.failed_hour[now.hour] += 1
                failed_trips.add(v)
        elif kind == "return":
            if v in failed_trips:
                continue
            s = day.trips[v][3]
            if closed(s, now, "return"):
                if s not in near:
                    a = day.stations[s]
                    near[s] = sorted((o for o in day.stations if o != s),
                                     key=lambda o: haversine_m(a.lat, a.lon, day.stations[o].lat, day.stations[o].lon))
                s = next((o for o in near[s] if not closed(o, now, "return")), None)
                if s is None:  # 구역 전체가 닫혔으면 구역 밖에 반납한 것으로 본다
                    continue
                res.diverted_returns += 1
            change(s, now, +1)
        elif kind == "plan":
            v, shift_end = v
            snapshot = [Station(s, st.name, st.lat, st.lon, st.docks, stock[s]) for s, st in day.stations.items()]
            blocked = frozenset(s for s, cl in day.closures.items() if any(a < v and b > now for a, b, _ in cl))  # 이 계획이 도는 동안 닫히는 곳
            if carry:
                for i in range(trucks):
                    if i not in fleet:  # 근무조 시작: 거점에서 예비 자전거를 반쯤 싣고 나간다
                        n = min(truck_capacity // 2, depot_bikes)
                        depot_bikes -= n
                        load[(i, now)] = n
                        fleet[i] = ((i, now), None, now)
                avail = [Truck(i, truck_capacity, load=load[rk], position=pos,
                               available_time_s=max(0, int((shift_end - max(now, ready)).total_seconds())))
                         for i, (rk, pos, ready) in sorted(fleet.items())]
                plan: Plan = policy.plan(now, snapshot, day.depot, avail, truck_capacity, blocked=blocked)
            else:
                plan = policy.plan(now, snapshot, day.depot, trucks, truck_capacity, blocked=blocked)
            for r in plan.routes:
                if carry:
                    old, pos, t = fleet[r.truck_id]
                    rk = (r.truck_id, now)
                    load[rk] = load.pop(old)
                    pos, t = pos or day.depot, max(now, t)
                else:
                    start_load = min(r.start_load, depot_bikes)
                    depot_bikes -= start_load
                    rk = (r.truck_id, now)
                    load[rk] = start_load
                    t, pos = now, day.depot
                t0 = t
                for stop in r.stops:
                    st = day.stations[stop.station_id]
                    arrive = t + timedelta(minutes=leg_minutes(pos, (st.lat, st.lon)))
                    if arrive >= v:  # 다음 계획(또는 근무조 끝)까지 못 가는 정류장은 버린다
                        break
                    heapq.heappush(q, (arrive, 2, "stop", (rk, stop.station_id, stop.action, stop.bikes)))
                    t = arrive + timedelta(minutes=stop_minutes(stop.bikes))
                    pos = (st.lat, st.lon)
                if carry and v < shift_end:  # 근무조 안: 마지막으로 들른 곳에서 다음 계획을 기다린다
                    fleet[r.truck_id] = (rk, pos, t)
                    res.truck_h += (t - t0).total_seconds() / 3600
                    continue
                fleet.pop(r.truck_id, None)
                back = min(t + timedelta(minutes=leg_minutes(pos, day.depot)), shift_end if carry else v)
                res.truck_h += (back - t0).total_seconds() / 3600
                heapq.heappush(q, (back, 1.5, "unload", rk))
            if carry and v >= shift_end:  # 경로 없이 근무조가 끝난 트럭도 거점으로 돌아가 내린다
                for i, (rk, pos, ready) in list(fleet.items()):
                    back = min(ready + timedelta(minutes=leg_minutes(pos or day.depot, day.depot)), shift_end)
                    heapq.heappush(q, (max(back, now), 1.5, "unload", rk))
                    fleet.pop(i)
        elif kind == "stop":
            rk, s, action, bikes = v
            if closed(s, now, "rent") or closed(s, now, "return"):  # 닫힌 대여소에는 트럭도 일하지 않는다
                res.skipped_stops += 1
            elif action == "pickup":
                n = min(bikes, stock[s], truck_capacity - load[rk])
                load[rk] += n
                res.moved += n
                res.pick_short += bikes - n
                change(s, now, -n)
            else:
                n = min(bikes, load[rk])
                load[rk] -= n
                res.dropped += n
                change(s, now, +n)
        elif kind == "unload":
            depot_bikes += load.pop(v)
        elif kind == "ops":
            s, dx = v
            n = max(dx, -stock[s])  # 회수는 있는 만큼만
            if same_supply:
                n = min(n, depot_bikes)
                depot_bikes -= n
            if dx < 0:
                res.moved -= n
                res.pick_short += n - dx
            else:
                res.dropped += n
            change(s, now, n)

    for s in stock:  # 하루 끝까지 남은 시간을 지표에 더한다
        change(s, end, 0)
    res.stock = stock
    return res


def poisson(rng: random.Random, lam: float) -> int:
    if lam > 30:  # 큰 평균은 정규 근사
        return max(0, round(rng.gauss(lam, math.sqrt(lam))))
    k, p, floor = 0, 1.0, math.exp(-lam)
    while True:
        p *= rng.random()
        if p <= floor:
            return k
        k += 1


def resample_demand(day: Day, seed: int) -> Day:
    """기록된 대여를 (대여소, 시각)마다 포아송으로 다시 뽑은 하루. 평균은 그 시각의 기록 건수, 시각은 그 시간 안에서 고르게,
    반납 대여소와 이용 시간은 같은 (대여소, 시각)의 기록 하나를 본뜬다. 구역 밖·전날 빌린 대여의 반납과 억눌린 수요는 그대로 둔다.
    왜: 기록된 대여는 실제 트럭이 채워 둔 자전거로 일어났다. 그대로 재생하면 실제 트럭 이동(현행 운영 재현)과 대여가
    한 대 한 대 맞물려 현행 운영이 유리하다. 수요 수준은 그날과 같게 두어, 예측기가 본 그날의 정보와 어긋나지 않게 한다."""
    rng = random.Random(seed)
    n = len(day.trips) if day.n_recorded is None else day.n_recorded
    groups, keep = defaultdict(list), []
    for t in day.trips[:n]:
        (groups[(t[1], t[0].hour)] if t[0] is not None else keep).append(t)
    new = []
    for key in sorted(groups):  # 순서를 고정해야 같은 시드로 같은 하루가 나온다
        ts = groups[key]
        for _ in range(poisson(rng, len(ts))):
            rt0, rs, ut0, us0 = rng.choice(ts)
            rt = rt0.replace(minute=0, second=0, microsecond=0) + timedelta(seconds=rng.uniform(0, 3600))
            new.append((rt, rs, None if ut0 is None else rt + (ut0 - rt0), us0))
    trips = keep + new
    return replace(day, trips=trips + day.trips[n:], n_recorded=len(trips))


SCOPES = {"대여 중단": "rent", "반납 중단": "return", "대여·반납 중단": "both", "대여소 폐쇄": "both", "전체 운영 중단": "both"}


def load_closures(path: Path, d: date, stations: dict[int, Station], names: dict[str, int], near: dict[str, list[int]]) -> dict:
    """d일의 대여소 일시 운영 중단을 읽는다. 파일 형식: date,start,end,scope,area,stations,station_count,reason,source
    (데스크톱이 공지에서 모은 표, analysis/in/closures.csv). 반환: Day.closures (구역 대여소만).
    가정: stations 열(대여소 이름 또는 번호, ;로 구분)이 있으면 그 대여소.
          stations가 비고 station_count만 있으면 area 행사장 700m 안 대여소 중 행사장에서 가까운 순으로 station_count곳
          (보신각: 700m 안 39곳, 공지는 14~16곳이고 대여 기록에서도 닫힌 곳이 종각 쪽에 몰려 있다).
          둘 다 없으면 700m 안 전부. 공지 하나의 수가 여러 지역 합이면(탄핵 선고 71곳 = 안국역·광화문·국회·한남동)
          지역마다 min(수, 그 지역 대여소 수)라 사실상 전부 닫히는데, 합 76곳 ≈ 71곳이라 그대로 둔다.
          area가 '서울 전체'이거나 scope가 '전체 운영 중단'이면 구역 모든 대여소. start·end가 비면 하루 종일,
          끝이 시작보다 이르거나 같으면 자정까지. 이름은 공백을 빼고 대여소 마스터 이름(names)과 맞춘다.
          station_count가 적힌 이름 수보다 많으면 지역 전체가 닫힌 것으로 보고 area 행사장 700m 안 대여소를 더한다.
          station_count는 같은 날·같은 reason의 줄을 합친 수와 비교해, 다르면 경고만 낸다(공지 하나가 여러 지역에 걸칠 때).
          같은 대여소에 여러 줄이 겹쳐도 그대로 모두 적용한다.
    names: 공백을 뺀 대여소 이름 → 대여소번호(서울 전체), near: 행사장 → 700m 안 대여소번호(가까운 순)"""
    if not path.exists():
        return {}
    t0 = datetime.combine(d, time())
    out: dict[int, list] = defaultdict(list)
    groups: dict[str, tuple[set, int]] = {}  # reason → (닫은 대여소 합, 공지의 대여소 수)
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for i, r in enumerate(rows, start=2):
        if r["date"] != d.isoformat():
            continue
        if r["scope"] not in SCOPES:  # 손으로 모은 표라 모르는 값은 조용히 넘기지 않는다
            raise ValueError(f"{path} {i}행: scope는 {list(SCOPES)} 중 하나 ({r['scope']!r})")
        hm = lambda x: timedelta(hours=int(x[:2]), minutes=int(x[3:5])) if x else None  # noqa: E731
        a, b = hm(r["start"]) or timedelta(0), hm(r["end"]) or timedelta(days=1)
        b = b if b > a else timedelta(days=1)
        if r["area"] == "서울 전체" or r["scope"] == "전체 운영 중단":
            sts = set(stations)
        elif r["stations"].strip():
            toks = [x.strip() for x in r["stations"].split(";") if x.strip()]
            sts = set()
            for tok in toks:
                no = int(tok) if tok.isdigit() else names.get("".join(tok.split()))
                if no is None:
                    warnings.warn(f"{path} {i}행: 대여소 마스터에 없는 이름 {tok!r}")
                else:
                    sts.add(no)
            if r["station_count"].strip().isdigit() and int(r["station_count"]) > len(toks):  # 공지가 일부 이름만 적었다: 지역 전체가 닫혔다
                sts |= set(near.get(r["area"], []))
        else:
            n = int(r["station_count"]) if r["station_count"].strip().isdigit() else None
            sts = set(near.get(r["area"], [])[:n])
        if r["station_count"].strip().isdigit():
            g, n = groups.get(r["reason"], (set(), 0))
            groups[r["reason"]] = (g | sts, max(n, int(r["station_count"])))
        for st in sts & stations.keys():
            out[st].append((t0 + a, t0 + b, SCOPES[r["scope"]]))
    for reason, (g, n) in groups.items():
        if len(g) != n:
            warnings.warn(f"{path} {d} {reason}: 닫은 대여소 {len(g)}곳(적힌 곳 + 행사장 700m 안)이 공지의 {n}곳과 다르다")
    return dict(out)


def load_day(terr: str, d: date, latent: bool = True, sources_weeks: int | None = None) -> Day:
    """구역 terr의 d일 하루를 파일에서 만든다. latent이면 실제로 비어 있던 시간의 억눌린 수요를 trips 뒤에 더한다.
    sources_weeks를 주면 공급처를 전해 4~10월 대신 그날 전 그 주 수만큼의 역산 방문으로 고른다(그날 0시 전에 끝난 방문만).

    입력: analysis/out/stations.csv, data/shared/<구역>/trips.parquet (export_region.py),
          analysis/rebal/out/gaps_<해>.parquet·bike_pick·bike_drop (초기 재고 복원. 2024·2025년),
          analysis/in/closures.csv (대여소 일시 운영 중단, load_closures)
    """
    import duckdb  # 시험(test_engine)은 파일 없이 돌도록 여기서만 쓴다

    t0 = datetime.combine(d, time())
    t1 = t0 + timedelta(days=1)
    reb = ROOT / "analysis/rebal/out"
    yr = "" if d.year == 2025 else f"_{d.year}"  # 역산 파일 이름: 2025년은 해 표시가 없다(bike_pick.parquet)
    c = duckdb.connect()
    c.execute(f"CREATE TABLE zs AS SELECT * FROM '{ROOT}/analysis/out/stations.csv' WHERE terr = ?", [terr])
    c.execute(f"""CREATE VIEW tr AS SELECT rent_dt, rent_st, rtn_dt, rtn_st FROM '{ROOT}/data/shared/{terr}/trips.parquet'
                  WHERE rtn_st IS NOT NULL""")
    # 초기 재고: 하루 시작 시각에 서 있는 자전거. 트럭이 옮긴 자전거는 회수 창 중간 전에는 원래 대여소, 배치 창 중간 뒤에는 새 대여소
    stock0 = dict(c.execute(f"""
    WITH g AS (SELECT * FROM '{reb}/gaps_{d.year}.parquet' WHERE t_from <= $t0 AND t_to > $t0),
    p AS (SELECT bike_id, t_to, lo + (hi - lo) / 2 AS m FROM '{reb}/bike_pick{yr}.parquet'),
    d AS (SELECT bike_id, t_to, lo + (hi - lo) / 2 AS m FROM '{reb}/bike_drop{yr}.parquet')
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
    raw = c.execute("""SELECT rent_dt, rent_st, rtn_dt, rtn_st FROM tr
                       WHERE (rent_dt >= $a AND rent_dt < $b) OR (rtn_dt >= $a AND rtn_dt < $b) ORDER BY rent_dt""",
                    {"a": t0, "b": t1}).fetchall()
    n_recorded = None
    if latent:
        from .latent import latent_trips

        holidays = {r[0] for r in c.execute(f"SELECT date::DATE FROM read_csv('{ROOT}/data/shared/{terr}/holidays.csv')").fetchall()}
        n_recorded = len(raw)
        raw += latent_trips(c, reb, t0, holidays, set(stations), seed=terr)
    trips = [(rt if t0 <= rt < t1 and rs in stations else None, rs if rs in stations else None,
              ut if t0 <= ut < t1 and us in stations else None, us if us in stations else None)
             for rt, rs, ut, us in raw]
    lat = sum(s.lat for s in stations.values()) / len(stations)
    lon = sum(s.lon for s in stations.values()) / len(stations)
    fixed = frozenset(r[0] for r in c.execute("SELECT st_no FROM zs WHERE role = '고정 회수'").fetchall()) & stations.keys()
    # 역산 방문은 시각 창의 중간에 일어났다고 본다(초기 재고 복원과 같은 기준)
    visits, visits_prev_week = ([(m + shift, st, -n if kind == "pick" else n) for m, st, kind, n in c.execute(f"""
        SELECT lo + (hi - lo) / 2 AS m, st, kind, n::INT FROM '{ROOT}/data/shared/{terr}/rebal_visits.parquet'
        WHERE lo + (hi - lo) / 2 >= $a AND lo + (hi - lo) / 2 < $b ORDER BY m""", {"a": t0 - shift, "b": t1 - shift}).fetchall()
              if st in stations] for shift in (timedelta(0), timedelta(days=7)))
    # 공급처: 하루 평균 회수 − 배치가 2대 이상인 대여소. 기본은 전해 4~10월(성수기) 역산 방문이라 재생하는 해의 자료를 쓰지 않는다
    def pick_sources(weeks):
        if weeks:
            a, days = t0 - timedelta(weeks=weeks), 7 * weeks
            where = f"lo >= '{a}' AND hi < '{t0}'"
        else:
            days, where = 214, f"lo >= '{d.year - 1}-04-01' AND lo < '{d.year - 1}-11-01'"
        return frozenset(r[0] for r in c.execute(f"""
            SELECT st FROM '{ROOT}/data/shared/{terr}/rebal_visits.parquet' WHERE {where}
            GROUP BY st HAVING sum(CASE WHEN kind = 'pick' THEN n ELSE -n END) / {days}.0 >= 2""").fetchall()) & stations.keys()

    sources = pick_sources(sources_weeks)
    if not sources and not sources_weeks:  # 전해 역산이 없으면(2024년 날짜: 2023 자료 없음) 그날 전 8주로 고른다
        sources = pick_sources(8)
    names = {"".join(str(n).split()): no for no, n in c.execute(f"SELECT st_no, st_name FROM '{ROOT}/analysis/out/stations.csv'").fetchall()}
    near: dict[str, list[int]] = defaultdict(list)
    for v, no in c.execute(f"SELECT venue, st_no FROM '{ROOT}/analysis/out/event_stations.csv' ORDER BY venue, dist_m, st_no").fetchall():
        near[v].append(no)
    closures = load_closures(ROOT / "analysis/in/closures.csv", d, stations, names, near)
    return Day(t0, stations, trips, (lat, lon), fixed, sources, visits, visits_prev_week, n_recorded, closures)
