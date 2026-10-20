# Author: Jung Minkyu
# Date: 20OCT2026
# Purpose: 재배치 파이프라인의 모든 단계가 함께 쓰는 데이터 모양(Station, Forecast, Imbalance, Plan 등)을 정의한다.

from __future__ import annotations

from dataclasses import dataclass, field
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
    operating_limit: int | None = None  # 운영상 목표 상한. None이면 거치대 수를 초기값으로 사용


@dataclass(frozen=True)
class RoutingStation:
    """Location for an already computed demand; producer validates inventory."""

    id: int
    name: str
    lat: float
    lon: float


@dataclass(frozen=True)
class Truck:
    """재배치 트럭 한 대."""

    id: int
    capacity: int = 15  # PRD v0.2 초기값 (실제 운영 검증 전의 설정)
    load: int = 0  # 현재 적재량. 솔버가 임의로 출발 적재량을 선택하지 않는다
    position: tuple[float, float] | None = None  # (위도, 경도), None이면 차고지
    available_time_s: int = 3 * 60 * 60  # 요청 시점부터 근무 종료까지 남은 시간
    fixed_station_ids: tuple[int, ...] = ()  # 처리할 때 차량을 고정. 방문 순서·방문 필수는 지정하지 않음


@dataclass(frozen=True)
class Forecast:
    """한 대여소의 '예측 구간' 동안의 예상 수요. AI 모델이 내놓는 값이다.

    예측 구간 = 예측값이 몇 시간 동안의 대여·반납 수인지를 정하는 시간 길이(N시간).
    이 클래스는 N을 정하지 않는다. 예측기가 쓰는 시간 단위가 곧 N이다.
    (mock 예측기는 hour 단위로 예측하므로 N=1시간이다.)
    rentals와 returns는 반드시 같은 구간을 기준으로 해야 한다.
    """

    rentals: int  # 예측 구간 동안 대여되어 대여소에서 나갈 것으로 예상되는 자전거 수
    returns: int  # 예측 구간 동안 반납되어 대여소로 들어올 것으로 예상되는 자전거 수


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
    priority: int | float | None = None  # 공통 명세의 비음수 점수. None이면 수량 × 유형 가중치

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
    arrival_s: int = 0  # 요청 기준 시각으로부터 도착까지의 초
    departure_s: int = 0  # 도착 시각 + 작업 시간


@dataclass
class Route:
    """트럭 한 대의 작업 경로. 거점에서 출발해 stops를 순서대로 돌고 거점으로 돌아온다."""

    truck_id: int
    start_load: int  # 거점에서 출발할 때 트럭에 실려 있는 자전거 수
    stops: list[Stop] = field(default_factory=list)  # 방문 순서대로 쌓인다
    distance_m: int = 0  # 거점 출발 ~ 거점 복귀까지의 총 이동거리(미터)
    duration_s: int = 0  # 현재 위치에서 출발해 작업 후 차고지로 복귀할 때까지


@dataclass
class Plan:
    """최종 결과물: 모든 트럭의 경로 + 처리하지 못한 불균형."""

    routes: list[Route]
    unserved: list[Imbalance]  # 미처리 거점. 원인은 unserved_reasons를 확인한다
    solver: str  # 어떤 방식으로 만든 계획인지 ("ortools" 또는 "greedy")
    unserved_reasons: dict[int, str] = field(default_factory=dict)
    status: Literal["complete", "partial", "no_solution"] = "complete"

    @property
    def total_distance_m(self) -> int:
        # 모든 트럭의 이동거리 합
        return sum(r.distance_m for r in self.routes)

    @property
    def bikes_moved(self) -> int:
        # 회수·보충 작업 대수의 합. 같은 자전거를 양쪽에서 셀 수 있으며 고유 대수가 아니다
        return sum(s.bikes for r in self.routes for s in r.stops)

    @property
    def unserved_bikes(self) -> int:
        # 처리하지 못하고 남는 자전거 수(남는 것/모자란 것 모두 절댓값으로 합산)
        return sum(abs(i.delta) for i in self.unserved)
