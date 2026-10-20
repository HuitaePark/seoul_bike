# Author: Jung Minkyu
# Date: 20OCT2026
# Purpose: Demand prediction interface plus a heuristic mock to be replaced by the team's AI model.

"""Demand forecasting layer.

The team's real model (e.g. LSTM / XGBoost on rental history + weather) plugs in
by implementing `DemandPredictor`. `HeuristicPredictor` is a mock with the same
interface so the optimizer can be developed and tested without a trained model.
"""
from __future__ import annotations

import random
from typing import Protocol

from .models import Forecast, Station


class DemandPredictor(Protocol):
    def predict(self, station: Station, hour: int) -> Forecast: ...


# Station kind drives the mock commute pattern: business areas receive bikes in
# the morning and lose them in the evening; residential areas do the opposite.
_COMMUTE_IN = range(7, 10)
_COMMUTE_OUT = range(17, 21)


class HeuristicPredictor:
    def __init__(self, kinds: dict[int, str], seed: int = 0):
        self._kinds = kinds
        self._rng = random.Random(seed)

    def predict(self, station: Station, hour: int) -> Forecast:
        base = max(2, station.capacity // 5)
        peak = station.capacity // 2
        kind = self._kinds.get(station.id, "leisure")

        rentals = returns = base
        if kind == "business":
            if hour in _COMMUTE_IN:
                returns += peak
            elif hour in _COMMUTE_OUT:
                rentals += peak
        elif kind == "residential":
            if hour in _COMMUTE_IN:
                rentals += peak
            elif hour in _COMMUTE_OUT:
                returns += peak

        jitter = lambda v: max(0, v + self._rng.randint(-1, 1))  # noqa: E731
        return Forecast(rentals=jitter(rentals), returns=jitter(returns))
