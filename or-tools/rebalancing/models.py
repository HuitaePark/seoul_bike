# Author: Jung Minkyu
# Date: 20OCT2026
# Purpose: 재배치 파이프라인의 모든 단계가 함께 쓰는 데이터 모양(Station, Forecast, Imbalance, Plan 등)을 정의한다.

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

# [읽는 법] 이 파일은 "데이터를 담는 그릇"만 정의한다. 계산 로직은 없다.
# 데이터가 흘러가는 순서:
#   Station(지금 상태) + Forecast(예측) -> Imbalance(얼마나 옮길까)
#   -> (OR-Tools) -> Stop / Route / Plan(트럭 작업 계획)
#
# [frozen=True 란?] 객체를 만든 뒤에는 값을 바꿀 수 없게 막는 옵션이다.
# 계산 도중 실수로 값이 바뀌는 사고(특히 예측값이 바뀌는 것)를 방지한다.


@dataclass(frozen=True)
class Station:
    """대여소 한 곳의 '지금 상태'. 예측이 아니라 관측된 사실이다."""

    id: int
    name: str
    lat: float  # 위도
    lon: float  # 경도
    capacity: int  # 대여소 용량: 자전거를 세울 수 있는 거치대 수
    bikes: int  # 지금 대여소에 세워져 있는 자전거 수


@dataclass(frozen=True)
class Truck:
    """재배치 트럭 한 대."""

    id: int
    capacity: int  # 트럭에 한 번에 실을 수 있는 자전거 수


@dataclass(frozen=True)
class Forecast:
    """한 대여소의 '예측 구간' 동안의 예상 수요. AI 모델이 내놓는 값이다.

    예측 구간 = start부터 horizon_h시간. 값은 1시간 단위로 나눠 담는다.
    rentals_h[i], returns_h[i]는 start + i시간부터 1시간 동안의 값이다(길이 = horizon_h).
    시간별로 담는 이유: 구간 합계만 보면 "아침에 비었다가 저녁에 다시 차는" 대여소를 정상으로 본다(PRD 7.1 궤적).
    값은 기댓값이라 실수일 수 있다. 정수 대수가 필요한 곳에서 반올림한다.
    """

    start: datetime  # 예측 구간 시작 시각(정시)
    rentals_h: tuple[float, ...]  # 시간별로 대여되어 대여소에서 나갈 것으로 예상되는 자전거 수
    returns_h: tuple[float, ...]  # 시간별로 반납되어 대여소로 들어올 것으로 예상되는 자전거 수
    model: str  # 어떤 예측기가 만든 값인지. 거점별 근거(FR-03)에 표시한다

    @property
    def rentals(self) -> float:
        # 예측 구간 전체의 예상 대여 수(합계)
        return sum(self.rentals_h)

    @property
    def returns(self) -> float:
        # 예측 구간 전체의 예상 반납 수(합계)
        return sum(self.returns_h)


@dataclass(frozen=True)
class Imbalance:
    """대여소의 불균형 정도. 자전거가 남는지(+) 모자란지(-)를 숫자 하나로 나타낸다.

    delta > 0 : 자전거가 남는다 -> 트럭이 가서 delta대를 '수거(pickup)'해야 한다.
    delta < 0 : 자전거가 모자란다 -> 트럭이 가서 |delta|대를 '보충(dropoff)'해야 한다.
    예) delta=+6 이면 6대 수거, delta=-9 이면 9대 보충.
    정상인 대여소는 Imbalance로 만들어지지 않는다(OR-Tools는 불균형인 곳만 본다).
    """

    station_id: int  # 대여소 고유 번호
    delta: int  # 남는 양(+) 또는 모자란 양(-)

    @property
    def action(self) -> Literal["pickup", "dropoff"]:
        # delta의 부호로 트럭이 해야 할 행동을 정한다.
        return "pickup" if self.delta > 0 else "dropoff"


@dataclass
class Stop:
    """트럭이 경로 중에 들르는 정거장 한 곳."""

    station_id: int
    action: Literal["pickup", "dropoff"]  # 수거 또는 보충
    bikes: int  # 이 정거장에서 옮기는 자전거 수 (항상 양수)
    load_after: int  # 이 정거장에서 일을 끝낸 뒤 트럭에 실려 있는 자전거 수


@dataclass
class Route:
    """트럭 한 대의 작업 경로. 거점에서 출발해 stops를 순서대로 돌고 거점으로 돌아온다."""

    truck_id: int
    start_load: int  # 거점에서 출발할 때 트럭에 실려 있는 자전거 수
    stops: list[Stop] = field(default_factory=list)  # 방문 순서대로 쌓인다
    distance_m: int = 0  # 거점 출발 ~ 거점 복귀까지의 총 이동거리(미터)


@dataclass
class Plan:
    """최종 결과물: 모든 트럭의 경로 + 처리하지 못한 불균형."""

    routes: list[Route]
    unserved: list[Imbalance]  # 시간·트럭이 부족해서 포기한 대여소들
    solver: str  # 어떤 방식으로 만든 계획인지 ("ortools" 또는 "greedy")

    @property
    def total_distance_m(self) -> int:
        # 모든 트럭의 이동거리 합
        return sum(r.distance_m for r in self.routes)

    @property
    def bikes_moved(self) -> int:
        # 이 계획대로 하면 옮기게 되는 자전거 총 수
        return sum(s.bikes for r in self.routes for s in r.stops)

    @property
    def unserved_bikes(self) -> int:
        # 처리하지 못하고 남는 자전거 수(남는 것/모자란 것 모두 절댓값으로 합산)
        return sum(abs(i.delta) for i in self.unserved)
