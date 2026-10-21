"""Translate forecast totals to quantities; operating targets are not dock limits."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from numbers import Real

from .forecast import DemandPredictor, HourlyForecast, forecast_window
from .models import Imbalance, Station
from .problem import require_int


def _demand(name: str, value: int | float | Decimal) -> Decimal:
    # Real includes NumPy scalar predictions without adding a NumPy dependency here.
    if isinstance(value, bool) or not isinstance(value, (Real, Decimal)):
        raise ValueError(f"{name} must be a finite demand in [0, 1e9]")
    number = Decimal(str(value))
    if not number.is_finite() or not 0 <= number <= 1_000_000_000:
        raise ValueError(f"{name} must be a finite demand in [0, 1e9]")
    return number


def healthy_band(
    station: Station, rentals: int | float | Decimal, returns: int | float | Decimal,
    safety: int, *, operating_limit: int | None = None,
) -> tuple[int, int]:
    """Integer starting inventory targets for END-of-window expected totals.

    Preserve fractional demand until the final band: ceil the minimum, floor
    the maximum. This never rounds a required replenishment or available pickup
    toward an unsafe integer. It does not check intermediate-hour trajectories.
    """
    net_outflow = _demand("rentals", rentals) - _demand("returns", returns)
    require_int("safety", safety)
    limit = operating_limit if operating_limit is not None else station.operating_limit
    if limit is None:
        limit = station.docks
    require_int("operating_limit", limit)
    lo = max(0, int((net_outflow + safety).to_integral_value(rounding=ROUND_CEILING)))
    hi = max(0, int((Decimal(limit) + net_outflow).to_integral_value(rounding=ROUND_FLOOR)))
    # Protect the minimum inventory when the soft upper target conflicts with it.
    return lo, max(lo, hi)


def _forecast_totals(forecast: HourlyForecast, start: datetime, horizon_h: int) -> tuple[Decimal, Decimal]:
    if forecast_window(getattr(forecast, "start", None), horizon_h) != start:
        raise ValueError("forecast start differs from the requested window")
    version = getattr(forecast, "model_version", None)
    if not isinstance(version, str) or not version.strip():
        raise ValueError("forecast model_version must be non-empty")
    totals = []
    for field in ("rentals_h", "returns_h"):
        values = getattr(forecast, field, None)
        if (not isinstance(values, Sequence) or isinstance(values, (str, bytes))
                or len(values) != horizon_h):
            raise ValueError(f"{field} must contain exactly horizon_h hourly values")
        # Sum decimal representations before rounding; 0.1+0.2 must not create an extra bicycle.
        totals.append(sum((_demand(f"{field}[{i}]", value) for i, value in enumerate(values)), Decimal(0)))
    return totals[0], totals[1]


def compute_imbalances(
    stations: list[Station], predictor: DemandPredictor, start: datetime,
    horizon_h: int = 3, safety: int = 2,
) -> list[Imbalance]:
    """Use hourly predictor outputs with the existing endpoint target policy.

    A naive start means KST; an aware start is converted before calling data
    predictors. The three-hour default follows agreement 16; explicit 1..5
    hour windows remain supported. Intermediate trajectories are a later policy.
    """
    start = forecast_window(start, horizon_h)
    require_int("safety", safety)
    if len({s.id for s in stations}) != len(stations):
        raise ValueError("duplicate station IDs")
    result = []
    for station in stations:
        require_int("station.bikes", station.bikes)
        forecast = predictor.predict(station, start, horizon_h)
        rentals, returns = _forecast_totals(forecast, start, horizon_h)
        lo, hi = healthy_band(station, rentals, returns, safety)
        if station.bikes > hi:
            result.append(Imbalance(station.id, station.bikes - hi))
        elif station.bikes < lo:
            result.append(Imbalance(station.id, station.bikes - lo))
    return result
