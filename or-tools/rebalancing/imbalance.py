"""Translate forecast totals to quantities; operating targets are not dock limits."""
from __future__ import annotations

from .forecast import DemandPredictor
from .models import Imbalance, Station
from .problem import require_int


def healthy_band(
    station: Station, rentals: int, returns: int, safety: int, *, operating_limit: int | None = None,
) -> tuple[int, int]:
    """Starting inventory target given END-of-window totals, not a risk trajectory.

    Dock count only supplies the initial operating target. A high rental forecast
    can require inventory above that target. Even an empty station may overflow
    the target when net returns exceed it; this function cannot eliminate that
    unavoidable excess and does not claim intermediate-time safety.
    """
    require_int("rentals", rentals)
    require_int("returns", returns)
    require_int("safety", safety)
    limit = operating_limit if operating_limit is not None else station.operating_limit
    if limit is None:
        limit = station.capacity
    require_int("operating_limit", limit)
    lo = max(0, rentals - returns + safety)
    hi = max(0, limit + rentals - returns)
    # Protect the minimum inventory when the soft upper target conflicts with it.
    return lo, max(lo, hi)


def compute_imbalances(
    stations: list[Station], predictor: DemandPredictor, hour: int, safety: int = 2,
) -> list[Imbalance]:
    result = []
    for station in stations:
        forecast = predictor.predict(station, hour)
        lo, hi = healthy_band(station, forecast.rentals, forecast.returns, safety)
        if station.bikes > hi:
            result.append(Imbalance(station.id, station.bikes - hi))
        elif station.bikes < lo:
            result.append(Imbalance(station.id, station.bikes - lo))
    return result
