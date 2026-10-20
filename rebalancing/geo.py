from __future__ import annotations

from math import asin, cos, radians, sin, sqrt


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    """Great-circle distance in whole metres (OR-Tools needs integer costs)."""
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return round(2 * 6_371_000 * asin(sqrt(a)))


def distance_matrix(points: list[tuple[float, float]]) -> list[list[int]]:
    return [[haversine_m(*a, *b) for b in points] for a in points]
