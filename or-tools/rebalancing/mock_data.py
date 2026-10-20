# Author: Jung Minkyu
# Date: 20OCT2026
# Purpose: 실제 API를 연결하기 전까지 쓰는 가짜 서울 대여소 30곳과 트럭을 만든다.

"""가짜(mock) 서울 대여소 데이터. 나중에 서울 열린데이터광장 실시간 API로 교체한다."""
from __future__ import annotations

import random

from .models import Station, Truck

# 지역 이름, 중심 위도, 중심 경도, 유형 (유형은 forecast.py의 mock 출퇴근 패턴을 결정한다)
#   business    = 업무지구: 아침에 반납이 몰리고 저녁에 대여가 몰린다
#   residential = 주거지:   아침에 대여가 몰리고 저녁에 반납이 몰린다
#   leisure     = 놀거리:   출퇴근 영향이 적다
_CLUSTERS = [
    ("강남", 37.4979, 127.0276, "business"),
    ("종로", 37.5704, 126.9921, "business"),
    ("여의도", 37.5219, 126.9245, "business"),
    ("홍대", 37.5563, 126.9236, "leisure"),
    ("잠실", 37.5133, 127.1002, "residential"),
]

DEPOT = (37.5665, 126.9780)  # 서울시청 인근 가상 물류 거점 (트럭이 출발하고 돌아오는 곳)


def make_stations(per_cluster: int = 6, seed: int = 7) -> tuple[list[Station], dict[int, str]]:
    """지역마다 per_cluster곳씩 대여소를 만든다. 5개 지역 x 6곳 = 30곳.

    seed가 같으면 항상 같은 대여소가 만들어진다(실험을 재현하기 위해서).
    반환값: (대여소 목록, {대여소 id: 유형})
    """
    rng = random.Random(seed)
    stations: list[Station] = []
    kinds: dict[int, str] = {}
    for name, lat, lon, kind in _CLUSTERS:
        for n in range(per_cluster):
            sid = len(stations)  # 0, 1, 2, ... 순서대로 id 부여
            capacity = rng.choice([15, 20, 25, 30])
            # 지역 중심에서 대략 ±1.5km 안에 무작위로 흩뿌린다 (위도 0.013도 ≈ 1.4km)
            stations.append(
                Station(
                    id=sid,
                    name=f"{name}-{n + 1:02d}",  # 예: 강남-01
                    lat=lat + rng.uniform(-0.013, 0.013),
                    lon=lon + rng.uniform(-0.016, 0.016),
                    capacity=capacity,
                    bikes=rng.randint(0, capacity),  # 현재 재고는 0~용량 사이 무작위
                )
            )
            kinds[sid] = kind
    return stations, kinds


def make_trucks(count: int = 3, capacity: int = 20) -> list[Truck]:
    """같은 용량의 트럭 count대를 만든다."""
    return [Truck(id=i, capacity=capacity) for i in range(count)]
