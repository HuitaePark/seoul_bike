"""Validate reference geography without inventing inventory, predictions or IDs."""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
from pathlib import Path

from .travel_time import validate_point

REFERENCE_ROOT = Path(__file__).resolve().parents[2] / "analysis/out"
REFERENCE_REGIONS = REFERENCE_ROOT / "ref_regions.csv"
REFERENCE_STATIONS = REFERENCE_ROOT / "ref_stations.csv"


def _rows(path: str | Path, required: set[str]) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames or []
        if len(set(columns)) != len(columns) or not required <= set(columns):
            raise ValueError(f"{Path(path).name}: missing or duplicate CSV columns")
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError(f"{Path(path).name}: inconsistent CSV row width")
    return rows


def _integer(name: str, value: str, *, minimum: int = 0) -> int:
    if not value.isascii() or not value.isdecimal():
        raise ValueError(f"{name} must be an unsigned decimal integer")
    parsed = int(value)
    if not minimum <= parsed <= 1_000_000_000:
        raise ValueError(f"{name} is out of range")
    return parsed


def _text(name: str, value: str) -> str:
    if not value or value != value.strip():
        raise ValueError(f"{name} must be non-empty without surrounding whitespace")
    return value


def _point(name: str, lat: str, lon: str) -> tuple[float, float]:
    try:
        point = (float(lat), float(lon))
    except ValueError as error:
        raise ValueError(f"{name} coordinates must be numbers") from error
    validate_point(name, point)
    return point


@dataclass(frozen=True)
class SeedStation:
    data_id: str
    api_id: str
    name: str
    region_id: str
    position: tuple[float, float]
    docks: int
    role: str

    def summary(self) -> dict:
        return {"dataStationId": self.data_id, "apiStationId": self.api_id, "name": self.name,
                "regionId": self.region_id, "lat": self.position[0], "lon": self.position[1],
                "docks": self.docks, "role": self.role}


@dataclass(frozen=True)
class RegionSeed:
    region_id: str
    name: str
    depot: tuple[float, float]
    trucks_min: int
    stations: tuple[SeedStation, ...]

    def id_map(self, *, source: str) -> dict[str, str]:
        if source == "data":
            return {station.data_id: station.api_id for station in self.stations}
        if source == "api":
            return {station.api_id: station.data_id for station in self.stations}
        raise ValueError("ID source must be data or api; wire messages use api_station_id")

    def summary(self) -> dict:
        latitudes = [station.position[0] for station in self.stations]
        longitudes = [station.position[1] for station in self.stations]
        return {"referenceOnly": True, "regionId": self.region_id, "name": self.name, "stationCount": len(self.stations),
                "referenceDepot": list(self.depot), "trucksMin": self.trucks_min,
                "bounds": {"minLat": min(latitudes), "maxLat": max(latitudes),
                           "minLon": min(longitudes), "maxLon": max(longitudes)},
                "stations": [station.summary() for station in self.stations]}


def load_region_seeds(regions_path: str | Path, stations_path: str | Path) -> dict[str, RegionSeed]:
    """Validate the entire reference set, including cross-region identity and counts."""
    regions = _rows(regions_path, {"region_id", "name", "stations", "depot_lat", "depot_lon", "trucks_min"})
    region_ids = [_text("region_id", row["region_id"]) for row in regions]
    if len(set(region_ids)) != len(region_ids):
        raise ValueError("duplicate region_id")
    if not regions:
        raise ValueError("reference must contain at least one region")
    grouped: dict[str, list[SeedStation]] = {region_id: [] for region_id in region_ids}
    required = {"station_id", "api_station_id", "name", "region_id", "lat", "lon", "docks", "role"}
    data_ids, api_ids = set(), set()
    for row in _rows(stations_path, required):
        data_id = _text("station_id", row["station_id"])
        _integer("station_id", data_id, minimum=1)
        api_id = _text("api_station_id", row["api_station_id"])
        if not api_id.startswith("ST-") or not api_id[3:].isascii() or not api_id[3:].isdecimal():
            raise ValueError("api_station_id must match ST- followed by digits")
        if data_id in data_ids or api_id in api_ids:
            raise ValueError("station_id and api_station_id must each be unique")
        data_ids.add(data_id)
        api_ids.add(api_id)
        region_id = _text("station region_id", row["region_id"])
        if region_id not in grouped:
            raise ValueError(f"unknown station region_id: {region_id}")
        if row["role"] not in ("FIXED_PICKUP", "FIXED_DROPOFF", "VARIABLE"):
            raise ValueError("unknown station role")
        grouped[region_id].append(SeedStation(data_id, api_id, _text("station name", row["name"]), region_id,
                                            _point("station", row["lat"], row["lon"]),
                                            _integer("docks", row["docks"], minimum=1), row["role"]))
    result = {}
    for region in regions:
        region_id = region["region_id"]
        stations = grouped[region_id]
        expected = _integer("region stations", region["stations"], minimum=1)
        if len(stations) != expected:
            raise ValueError(f"{region_id} region station count mismatch: expected {expected}, found {len(stations)}")
        result[region_id] = RegionSeed(region_id, _text("region name", region["name"]),
                                      _point("depot", region["depot_lat"], region["depot_lon"]),
                                      _integer("trucks_min", region["trucks_min"], minimum=1),
                                      tuple(sorted(stations, key=lambda station: int(station.data_id))))
    return result


def load_region_seed(regions_path: str | Path, stations_path: str | Path, region_id: str) -> RegionSeed:
    _text("region_id", region_id)
    regions = load_region_seeds(regions_path, stations_path)
    if region_id not in regions:
        raise ValueError(f"unknown region_id: {region_id}")
    return regions[region_id]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regions", type=Path, required=True)
    parser.add_argument("--stations", type=Path, required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--region")
    selection.add_argument("--all-regions", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.all_regions:
            regions = load_region_seeds(args.regions, args.stations)
            summary = {"referenceOnly": True, "regionCount": len(regions),
                       "stationCount": sum(len(seed.stations) for seed in regions.values()),
                       "regions": [seed.summary() for seed in regions.values()]}
        else:
            summary = load_region_seed(args.regions, args.stations, args.region).summary()
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
