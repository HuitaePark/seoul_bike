# Author: Jung Minkyu
# Date: 20OCT2026
# Purpose: Dataclasses shared by every layer of the rebalancing pipeline.

"""Domain models shared by every layer of the rebalancing pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class Station:
    id: int
    name: str
    lat: float
    lon: float
    capacity: int  # number of docks
    bikes: int  # bikes parked right now


@dataclass(frozen=True)
class Truck:
    id: int
    capacity: int  # bikes the truck can carry at once


@dataclass(frozen=True)
class Forecast:
    """Expected demand of one station for the next planning window."""

    rentals: int  # bikes that will leave the station
    returns: int  # bikes that will arrive at the station


@dataclass(frozen=True)
class Imbalance:
    """How many bikes must leave (+) or arrive (-) for a station to stay healthy."""

    station_id: int
    delta: int

    @property
    def action(self) -> Literal["pickup", "dropoff"]:
        return "pickup" if self.delta > 0 else "dropoff"


@dataclass
class Stop:
    station_id: int
    action: Literal["pickup", "dropoff"]
    bikes: int
    load_after: int  # bikes on the truck after this stop


@dataclass
class Route:
    truck_id: int
    start_load: int
    stops: list[Stop] = field(default_factory=list)
    distance_m: int = 0


@dataclass
class Plan:
    routes: list[Route]
    unserved: list[Imbalance]
    solver: str

    @property
    def total_distance_m(self) -> int:
        return sum(r.distance_m for r in self.routes)

    @property
    def bikes_moved(self) -> int:
        return sum(s.bikes for r in self.routes for s in r.stops)

    @property
    def unserved_bikes(self) -> int:
        return sum(abs(i.delta) for i in self.unserved)
