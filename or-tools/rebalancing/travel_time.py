"""Injectable travel matrices; no HTTP, database or Kafka dependency in the solver."""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil, isfinite
from typing import Protocol

from .geo import distance_matrix

LatLon = tuple[float, float]


def validate_point(name: str, point: LatLon) -> None:
    if not isinstance(point, (tuple, list)) or len(point) != 2:
        raise ValueError(f"{name} must be (latitude, longitude)")
    lat, lon = point
    if any(type(v) not in (int, float) or not isfinite(v) for v in point):
        raise ValueError(f"{name} has invalid coordinates")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError(f"{name} has invalid coordinates")


def normalized_points(points: list[LatLon]) -> list[LatLon]:
    result = []
    for i, point in enumerate(points):
        validate_point(f"points[{i}]", point)
        # No rounding: two distinct truck positions must not share a cache key.
        result.append(tuple(0.0 if v == 0 else float(v) for v in point))
    return result


@dataclass(frozen=True)
class TravelMatrix:
    """Rows and columns follow exactly the order of points passed to the provider.

    Distances are integer metres, durations integer seconds. Asymmetric matrices
    are supported. Unknown/unreachable cells must not be silently converted to 0.
    """

    distances_m: list[list[int]]
    durations_s: list[list[int]]
    source: str = "fixed"

    def validate(self, size: int) -> None:
        for name, matrix in (("distances_m", self.distances_m), ("durations_s", self.durations_s)):
            if not isinstance(matrix, (list, tuple)) or len(matrix) != size or any(
                not isinstance(row, (list, tuple)) or len(row) != size for row in matrix
            ):
                raise ValueError(f"{name} must be a {size} x {size} matrix")
            for i, row in enumerate(matrix):
                for j, value in enumerate(row):
                    if type(value) is not int or not 0 <= value <= 1_000_000_000:
                        raise ValueError(f"{name}[{i}][{j}] must be a non-negative integer <= 1e9")
                    if i == j and value != 0:
                        raise ValueError(f"{name} diagonal must be zero")


class TravelTime(Protocol):
    def matrix(self, points: list[LatLon]) -> TravelMatrix: ...


@dataclass(frozen=True)
class HaversineTravelTime:
    """Development estimate, NOT live road or traffic data. Replace via TravelTime."""

    speed_kph: float = 30.0

    def matrix(self, points: list[LatLon]) -> TravelMatrix:
        if not isfinite(self.speed_kph) or self.speed_kph <= 0:
            raise ValueError("speed_kph must be finite and positive")
        distances = distance_matrix(points)
        speed_mps = self.speed_kph / 3.6
        return TravelMatrix(
            distances_m=distances,
            durations_s=[[ceil(value / speed_mps) for value in row] for row in distances],
            source="haversine-estimate",
        )
