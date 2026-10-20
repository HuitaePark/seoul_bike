# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: 시뮬레이터가 비교하는 재배치 정책. 근무조 시작마다 트럭 경로(Plan)를 내거나, 정해진 대여소 사건을 낸다.

"""재배치 정책 (PRD 9.1). 이름은 공개 API의 Policy 값과 같다.

  NONE         NoRebalancing  재배치 없음
  GREEDY       SimpleRule     단순 규칙: 지금 재고만 보고 빈 곳은 채우고, 고정 회수 대여소가 넘치면 뺀다. 경로는 가까운 순 탐욕(plan_greedy)
  CURRENT_OPS  CurrentOps     현행 운영 재현: 대여이력에서 역산한 트럭 방문을 그대로 재생
본 시스템(AGENT: 예측 → 목표 재고 → OR-Tools)은 M5에 같은 모양으로 더한다.
"""
from __future__ import annotations

from datetime import datetime

from rebalancing.baseline import plan_greedy
from rebalancing.models import Imbalance, Plan, Station, Truck

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
