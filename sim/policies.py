# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: 시뮬레이터가 비교하는 재배치 정책. 근무조 시작마다 트럭 경로(Plan)를 내거나, 정해진 대여소 사건을 낸다.

"""재배치 정책 (PRD 9.1). 이름은 공개 API의 Policy 값과 같다.

  NONE         NoRebalancing  재배치 없음
  GREEDY       SimpleRule     단순 규칙: 지금 재고만 보고 빈 곳은 채우고, 고정 회수 대여소가 넘치면 뺀다. 경로는 가까운 순 탐욕(plan_greedy)
  CURRENT_OPS  CurrentOps     현행 운영 재현: 대여이력에서 역산한 트럭 방문을 그대로 재생
  AGENT        Agent          본 시스템: 예측 → 목표 재고(재고 궤적) → OR-Tools 경로. 시뮬레이터에서는 LLM·승인 없이 자동 승인
"""
from __future__ import annotations

import math
from datetime import datetime
from itertools import accumulate

from rebalancing.baseline import plan_greedy
from rebalancing.models import Imbalance, Plan, Station, Truck
from rebalancing.planner import plan_routes

from .engine import SHIFTS, SimTravelTime

LOW = 2  # 단순 규칙: 재고가 이 이하면 빈 위험 (초기값)
MAX_LEAD = 5  # 예측기가 내다보는 최대 시간(forecast/nowcast.py, 합의 29번)
FILL_MIN = 5  # 빈 위험 대여소는 max(거치대 절반, 이 값)까지 채운다 (초기값)


def depot_trucks(n: int, capacity: int) -> list[Truck]:
    # 거점 예비 자전거로 반쯤 싣고 출발한다(엔진의 depot_bikes와 같은 가정). 5의 Truck은 출발 적재량을 솔버가 고르지 않는다
    return [Truck(i, capacity, load=capacity // 2) for i in range(n)]


class Policy:
    """정책의 기본 모양. 근무조 시작마다 plan()을 부르고, events()의 대여소 사건은 그 시각에 그대로 반영한다."""

    def plan(self, now: datetime, stations: list[Station], depot, trucks: int, truck_capacity: int,
             blocked: frozenset[int] = frozenset()) -> Plan:
        # blocked: 이 계획이 도는 동안 운영을 멈추는 대여소(엔진의 Day.closures). 트럭이 들르지 않게 최적화기에 넘긴다
        return Plan(routes=[], unserved=[], solver="none")

    def events(self, day) -> list[tuple[datetime, int, int]]:
        return []


class NoRebalancing(Policy):
    pass


class SimpleRule(Policy):
    """회수는 고정 회수 대여소에서만 한다(2026-10-20 결정). 넘치는 곳을 모두 빼면 곧 빠져나갈 자전거까지 가져가
    (아침 주거지, 낮 업무지구) 재배치 없음보다 실패 대여가 늘었다."""

    def __init__(self, fixed_pickup: frozenset[int]):
        self.fixed_pickup = fixed_pickup

    def plan(self, now: datetime, stations: list[Station], depot, trucks: int, truck_capacity: int,
             blocked: frozenset[int] = frozenset()) -> Plan:
        todo = []
        for s in stations:
            if s.bikes <= LOW:
                delta = s.bikes - max(s.capacity // 2, FILL_MIN)
            elif s.id in self.fixed_pickup and s.bikes > s.capacity:
                delta = s.bikes - s.capacity
            else:
                continue
            # 한 번에 트럭 용량을 넘게는 못 옮긴다(plan_greedy는 용량보다 큰 불균형을 아예 건너뛴다)
            todo.append(Imbalance(s.id, max(-truck_capacity, min(truck_capacity, delta))))
        return plan_greedy(depot, stations, todo, depot_trucks(trucks, truck_capacity), blocked_station_ids=blocked)


class CurrentOps(Policy):
    def events(self, day) -> list[tuple[datetime, int, int]]:
        return day.visits


class CurrentOpsLastWeek(Policy):
    """현행 운영 방식을 지난주 같은 요일의 역산 방문으로 재생한다(CURRENT_OPS_LW, 분석용).
    그날의 역산 방문은 나중에 실제로 대여가 일어난 곳에 놓인 배치(미래 정보)라서, 평소 하던 대로 움직인 운영과 비교하려고 둔다."""

    def events(self, day) -> list[tuple[datetime, int, int]]:
        return day.visits_prev_week


class Agent(Policy):
    """본 시스템. 계획할 때마다 지금 시각부터 근무조 끝까지(또는 horizon시간)의 시간별 예측으로 재고 궤적을 그려 목표를 정한다(PRD 7.1).
      배치  궤적이 'LOW + 여유' 아래로 내려가면 그만큼 채운다. 여유 = z × √(그때까지 예상 대여)(포아송 표준편차)
      회수  공급처(sources)에서, 궤적의 최고가 거치대 수를 넘을 때만, 빼도 궤적이 'LOW + 여유' 아래로 내려가지 않는 만큼 뺀다
    공급처를 정해 두는 이유: 대여소 하나의 예측은 우연에 의한 오차가 커서, 아무 곳에서나 예측만 믿고 빼면 그 오차만큼 실패가 생긴다
    (2025-06-04·11 강남-05에서 회수한 대여소의 실패가 재배치 없음보다 124~223건 늘었다). 공급처는 전해 역산의 회수 대여소다.
    pick_margin을 주면 공급처가 아닌 곳도 넘침이 크고 확실하면(궤적 − z_pick × √(예상 대여)의 최고 > 거치대 + pick_margin) 확실한 만큼 뺀다.
    pick_starve를 주면 이 회수는 공급(트럭 적재 + 공급처 회수)이 배치 요청의 그 비율보다 적은 계획에서만 넣는다.
    공급이 넉넉한 구역에서까지 빼면 뺀 곳의 실패가 더 늘었다(기준 묶음 강남-05 +118건).
    replan_h를 주면 엔진이 근무조 안에서 그 간격마다 다시 부른다(그 시각의 실제 재고와 새 예측으로).
    경로는 5의 plan_routes(OR-Tools)를 그대로 쓴다.
    경로 탐색은 5의 기본값을 따른다(해 50개에서 멈춤, 실행마다 같은 답). time_limit_s는 주지 않는다(주면 CPU 부하에 따라 답이 바뀐다).
    carry_trucks이면 엔진이 근무조 안에서 트럭의 현재 위치·적재량·남은 근무 시간을 Truck으로 넘긴다(PRD v0.2 재경로).
    아니면 다시 계획할 때마다 거점에서 반쯤 싣고 출발한다. sim_travel이면 최적화기에 엔진과 같은 이동 시간을 준다(합의 19번).
    fail_priority를 주면 대여소를 포기하는 벌점 점수를 대수 × 가중치 대신 실패 대여 기대값으로 둔다(PRD 우선순위):
      배치  그 대수를 지금 내려 막는 실패 대여 기대값(fails_avoided)   회수  대당 fail_priority(배치를 가능하게 하는 공급 가치)
    배치 필요가 실을 수 있는 자전거보다 훨씬 많아(계획마다 360~470대 vs 수십 대) 어디에 내릴지가 실패를 가른다."""

    def __init__(self, predictor, sources: frozenset[int], z: float = 1.0, replan_h: int | None = None,
                 horizon: int | None = None, time_limit_s: int | None = None, z_pick: float | None = None, bridge: bool = False,
                 shifts: tuple = SHIFTS, carry_trucks: bool = False, sim_travel: bool = False,
                 fail_priority: float | None = None, pick_margin: float | None = None, pick_starve: float | None = None):
        self.predictor = predictor
        self.sources = sources
        self.z = z  # 배치 여유
        self.z_pick = z if z_pick is None else z_pick  # 회수 여유. 크게 두면 공급처를 덜 비운다
        self.pick_margin = pick_margin
        self.pick_starve = pick_starve
        self.replan_h = replan_h
        self.horizon = horizon
        self.bridge = bridge  # 근무조 마지막 계획은 다음 근무조 시작까지(최대 MAX_LEAD시간) 버티게 본다
        self.time_limit_s = time_limit_s
        self.shifts = shifts  # 엔진도 이 근무조로 계획 시각을 잡는다
        self.carry_trucks = carry_trucks
        self.travel_time = SimTravelTime() if sim_travel else None
        self.fail_priority = fail_priority  # 회수 1대의 공급 가치(실패 대여 단위). None이면 5의 기본 점수

    def plan(self, now: datetime, stations: list[Station], depot, trucks: int, truck_capacity: int,
             blocked: frozenset[int] = frozenset()) -> Plan:
        h0, h1 = next((a, b) for a, b in self.shifts if a <= now.hour < b)
        horizon = self.horizon or h1 - now.hour  # 기본은 근무조 끝까지
        if self.bridge and now.hour + (self.replan_h or h1 - h0) >= h1:  # 이 근무조의 마지막 계획
            nxt = next((a for a, _ in self.shifts if a > now.hour), self.shifts[0][0] + 24)
            horizon = max(horizon, min(MAX_LEAD, nxt - now.hour))
        todo, extra = [], []  # extra: 공급처가 아닌 곳의 회수
        for s in stations:
            f = self.predictor.predict(s, now, horizon)
            traj = list(accumulate((r - n for r, n in zip(f.returns_h, f.rentals_h)), initial=s.bikes))
            sd = [0.0] + [math.sqrt(x) for x in accumulate(f.rentals_h)]
            need = max(LOW + self.z * d - t for t, d in zip(traj, sd))  # 이만큼 더 있어야 버틴다
            spare = -max(LOW + self.z_pick * d - t for t, d in zip(traj, sd))  # 빼도 버티는 양
            sure = max(t - self.z_pick * d for t, d in zip(traj, sd))  # 궤적 최고의 아래쪽 추정
            if need > 0:
                delta = -math.ceil(need)
            elif s.id in self.sources and s.capacity and max(traj) > s.capacity and spare > 0:
                delta = math.floor(min(spare, max(traj) - s.capacity))
            elif self.pick_margin is not None and s.capacity and sure > s.capacity + self.pick_margin and spare > 0:
                delta = math.floor(min(spare, sure - s.capacity))
            else:
                continue
            if delta:  # 회수량을 내림하면 0이 될 수 있다
                delta = max(-truck_capacity, min(truck_capacity, delta))
                prio = None  # 5의 기본 점수: 대수 × 가중치
                if self.fail_priority is not None:
                    var = list(accumulate((r + n for r, n in zip(f.rentals_h, f.returns_h)), initial=0.0))  # 포아송 분산: 대여 + 반납
                    prio = delta * self.fail_priority if delta > 0 else fails_avoided(traj, var, -delta)
                (todo if delta < 0 or s.id in self.sources else extra).append(Imbalance(s.id, delta, prio))
        fleet = trucks if isinstance(trucks, list) else depot_trucks(trucks, truck_capacity)  # carry_trucks면 엔진이 Truck 목록을 준다
        supply = sum(t.load for t in fleet) + sum(i.delta for i in todo if i.delta > 0)
        if self.pick_starve is None or supply < self.pick_starve * -sum(i.delta for i in todo if i.delta < 0):
            todo += extra
        return plan_routes(depot, stations, todo, fleet, time_limit_s=self.time_limit_s, travel_time=self.travel_time,
                           blocked_station_ids=blocked)


def exp_short(gap: float, var: float) -> float:
    """E[max(0, Y)], Y ~ N(gap, var). 재고보다 더 빠져나갈 대수의 기대값(포아송을 정규로 근사)."""
    if var <= 0:
        return max(0.0, gap)
    sd = math.sqrt(var)
    k = gap / sd
    return gap * 0.5 * (1 + math.erf(k / math.sqrt(2))) + sd * math.exp(-k * k / 2) / math.sqrt(2 * math.pi)


def fails_avoided(traj: list[float], var: list[float], bikes: int) -> float:
    """지금 bikes대를 내리면 막는 실패 대여 기대값. 실패 기대값은 궤적의 시각마다 E[모자란 대수]의 최대(하한)로 본다."""
    def fails(extra):
        return max(exp_short(-t - extra, v) for t, v in zip(traj, var))

    return max(0.0, float(fails(0) - fails(bikes)))  # 5는 파이썬 float만 받는다(예측값은 numpy float)
