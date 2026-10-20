# Author: Jung Minkyu
# Date: 20OCT2026
# Purpose: Converts demand forecasts into per-station pickup/dropoff quantities.

"""Turns forecasts into the per-station pickup/dropoff quantities the solver needs."""
from __future__ import annotations

from .forecast import DemandPredictor
from .models import Imbalance, Station


def healthy_band(station: Station, rentals: int, returns: int, safety: int) -> tuple[int, int]:
    """Stock range [lo, hi] at the start of the window that avoids both an empty
    station (nobody can rent) and a full one (nobody can return).

    end_stock = bikes - rentals + returns must satisfy safety <= end_stock <= capacity.
    """
    lo = min(max(0, rentals - returns + safety), station.capacity)
    hi = max(min(station.capacity, station.capacity + rentals - returns), 0)
    return lo, max(lo, hi)


def compute_imbalances(
    stations: list[Station], predictor: DemandPredictor, hour: int, safety: int = 2
) -> list[Imbalance]:
    result = []
    for s in stations:
        f = predictor.predict(s, hour)
        lo, hi = healthy_band(s, f.rentals, f.returns, safety)
        if s.bikes > hi:
            result.append(Imbalance(s.id, s.bikes - hi))  # surplus -> pick up
        elif s.bikes < lo:
            result.append(Imbalance(s.id, s.bikes - lo))  # shortage -> drop off (negative)
    return result
