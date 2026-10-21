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

from .engine import SHIFTS

LOW = 2  # 단순 규칙: 재고가 이 이하면 빈 위험 (초기값)
FILL_MIN = 5  # 빈 위험 대여소는 max(거치대 절반, 이 값)까지 채운다 (초기값)


class Policy:
    """정책의 기본 모양. 근무조 시작마다 plan()을 부르고, events()의 대여소 사건은 그 시각에 그대로 반영한다."""

    def plan(self, now: datetime, stations: list[Station], depot, trucks: int, truck_capacity: int) -> Plan:
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

    def plan(self, now: datetime, stations: list[Station], depot, trucks: int, truck_capacity: int) -> Plan:
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
        return plan_greedy(depot, stations, todo, [Truck(i, truck_capacity) for i in range(trucks)])


class CurrentOps(Policy):
    def events(self, day) -> list[tuple[datetime, int, int]]:
        return day.visits


class Agent(Policy):
    """본 시스템. 계획할 때마다 지금 시각부터 근무조 끝까지(또는 horizon시간)의 시간별 예측으로 재고 궤적을 그려 목표를 정한다(PRD 7.1).
      배치  궤적이 'LOW + 여유' 아래로 내려가면 그만큼 채운다. 여유 = z × √(그때까지 예상 대여)(포아송 표준편차)
      회수  공급처(sources)에서, 궤적의 최고가 거치대 수를 넘을 때만, 빼도 궤적이 'LOW + 여유' 아래로 내려가지 않는 만큼 뺀다
    공급처를 정해 두는 이유: 대여소 하나의 예측은 우연에 의한 오차가 커서, 아무 곳에서나 예측만 믿고 빼면 그 오차만큼 실패가 생긴다
    (2025-06-04·11 강남-05에서 회수한 대여소의 실패가 재배치 없음보다 124~223건 늘었다). 공급처는 전해 역산의 회수 대여소다.
    replan_h를 주면 엔진이 근무조 안에서 그 간격마다 다시 부른다(그 시각의 실제 재고와 새 예측으로).
    경로는 5의 plan_routes(OR-Tools)를 그대로 쓴다.
    shortcut: 포기 벌점이 우선순위 점수가 아니라 plan_routes의 대당 벌점이고, 시간 차원과 트럭 현재 위치 출발이 없어
              다시 계획할 때마다 거점에서 출발한다. 5가 OR-Tools를 PRD v0.2(우선순위 벌점, 시간 차원, 재경로)에 맞추면 그 입력으로 바꾼다."""

    def __init__(self, predictor, sources: frozenset[int], z: float = 1.0, replan_h: int | None = None,
                 horizon: int | None = None, time_limit_s: int = 2):
        self.predictor = predictor
        self.sources = sources
        self.z = z
        self.replan_h = replan_h
        self.horizon = horizon
        self.time_limit_s = time_limit_s

    def plan(self, now: datetime, stations: list[Station], depot, trucks: int, truck_capacity: int) -> Plan:
        horizon = self.horizon or next(h1 - now.hour for h0, h1 in SHIFTS if h0 <= now.hour < h1)  # 근무조 끝까지
        todo = []
        for s in stations:
            f = self.predictor.predict(s, now, horizon)
            traj = list(accumulate((r - n for r, n in zip(f.returns_h, f.rentals_h)), initial=s.bikes))
            margin = [0.0] + [self.z * math.sqrt(x) for x in accumulate(f.rentals_h)]
            need = max(LOW + m - t for t, m in zip(traj, margin))  # 이만큼 더 있어야 버틴다(음수면 남는 양)
            if need > 0:
                delta = -math.ceil(need)
            elif s.id in self.sources and s.capacity and max(traj) > s.capacity:
                delta = math.floor(min(-need, max(traj) - s.capacity))
            else:
                continue
            if delta:  # 회수량을 내림하면 0이 될 수 있다
                todo.append(Imbalance(s.id, max(-truck_capacity, min(truck_capacity, delta))))
        return plan_routes(depot, stations, todo, [Truck(i, truck_capacity) for i in range(trucks)], time_limit_s=self.time_limit_s)
