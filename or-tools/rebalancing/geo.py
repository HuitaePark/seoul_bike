# Author: Jung Minkyu
# Date: 20OCT2026
# Purpose: 두 좌표 사이 거리(하버사인)를 계산해서, OR-Tools가 요구하는 정수 거리표를 만든다.

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    """위도·경도 두 점 사이의 직선(지구 표면 기준) 거리를 미터 단위 정수로 돌려준다.

    하버사인(haversine) 공식은 지구가 둥글다는 점을 반영해 두 좌표 사이 거리를 구한다.
    정수로 반올림하는 이유: OR-Tools는 비용(거리)을 정수로만 받는다.
    주의: 실제 도로를 따라 달리는 거리가 아니라 '직선거리'라서 현실보다 짧게 나온다.
    """
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return round(2 * 6_371_000 * asin(sqrt(a)))  # 6,371,000m = 지구 반지름


def distance_matrix(points: list[tuple[float, float]]) -> list[list[int]]:
    """모든 점 쌍의 거리표를 만든다. 결과[i][j] = i번 점에서 j번 점까지의 거리(m)."""
    return [[haversine_m(*a, *b) for b in points] for a in points]
