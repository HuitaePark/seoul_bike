# Author: Jung Minkyu
# Date: 20OCT2026
# Purpose: Generates synthetic Seoul stations and trucks until the real API feed is connected.

"""Synthetic Seoul stations. Replace with the real Seoul Open Data API feed later."""
from __future__ import annotations

import random

from .models import Station, Truck

# name, lat, lon, kind (drives the mock commute pattern in forecast.py)
_CLUSTERS = [
    ("강남", 37.4979, 127.0276, "business"),
    ("종로", 37.5704, 126.9921, "business"),
    ("여의도", 37.5219, 126.9245, "business"),
    ("홍대", 37.5563, 126.9236, "leisure"),
    ("잠실", 37.5133, 127.1002, "residential"),
]

DEPOT = (37.5665, 126.9780)  # 서울시청 인근 가상 물류 거점


def make_stations(per_cluster: int = 6, seed: int = 7) -> tuple[list[Station], dict[int, str]]:
    rng = random.Random(seed)
    stations: list[Station] = []
    kinds: dict[int, str] = {}
    for name, lat, lon, kind in _CLUSTERS:
        for n in range(per_cluster):
            sid = len(stations)
            capacity = rng.choice([15, 20, 25, 30])
            # roughly +-1.5 km around the cluster centre
            stations.append(
                Station(
                    id=sid,
                    name=f"{name}-{n + 1:02d}",
                    lat=lat + rng.uniform(-0.013, 0.013),
                    lon=lon + rng.uniform(-0.016, 0.016),
                    capacity=capacity,
                    bikes=rng.randint(0, capacity),
                )
            )
            kinds[sid] = kind
    return stations, kinds


def make_trucks(count: int = 3, capacity: int = 20) -> list[Truck]:
    return [Truck(id=i, capacity=capacity) for i in range(count)]
