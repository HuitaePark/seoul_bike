"""Road-data coverage check for a prepared OSRM extract; NOT a performance or acceptance test.

Before measuring N01/300 stations on real roads, every station and the depot must snap to a road
within the solver's radius and reach each other in both directions. The solver itself rejects a whole
matrix on the first unreachable cell, so this lists which points fail instead.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sys

from .mock_data import DEPOT, make_stations
from .osrm import OsrmTravelTime, TravelTimeError
from .seeds import REFERENCE_REGIONS, REFERENCE_STATIONS, load_region_seed
from .travel_time import LatLon


@dataclass(frozen=True)
class Point:
    label: str
    position: LatLon
    depot: bool = False


def check(points: list[Point], road: OsrmTravelTime) -> dict:
    """Snap every point, then report directed unreachable pairs among the points that snapped."""
    unsnapped, snapped = [], []
    for point in points:
        distance = road.nearest_distance(point.position)
        if distance is None or distance > road.snap_radius_m:
            unsnapped.append({"label": point.label, "lat": point.position[0], "lon": point.position[1],
                              "nearestRoadM": None if distance is None else round(distance, 1)})
        else:
            snapped.append(point)
    # Several labels may share one coordinate; OSRM sees each coordinate once.
    groups: dict[LatLon, list[Point]] = {}
    for point in snapped:
        groups.setdefault(tuple(point.position), []).append(point)
    coordinates = list(groups)
    durations, version = road.reachability(coordinates) if len(coordinates) > 1 else ([[0]] * len(coordinates), None)
    size = len(coordinates)
    unreachable = []
    depot_rows = [i for i, c in enumerate(coordinates) if any(p.depot for p in groups[c])]
    for i, coordinate in enumerate(coordinates):
        outgoing = sum(1 for j in range(size) if j != i and durations[i][j] is None)
        incoming = sum(1 for j in range(size) if j != i and durations[j][i] is None)
        from_depot = any(durations[d][i] is None for d in depot_rows if d != i)
        to_depot = any(durations[i][d] is None for d in depot_rows if d != i)
        if outgoing or incoming:
            for point in groups[coordinate]:
                unreachable.append({"label": point.label, "lat": coordinate[0], "lon": coordinate[1],
                                    "unreachableTo": outgoing, "unreachableFrom": incoming,
                                    "depotToStationMissing": from_depot, "stationToDepotMissing": to_depot})
    pairs = sum(1 for i in range(size) for j in range(size) if i != j and durations[i][j] is None)
    return {
        "ok": not unsnapped and not unreachable,
        "pointCount": len(points),
        "distinctSnappedCoordinates": size,
        "snapRadiusM": road.snap_radius_m,
        "observedDataVersion": version,
        "unsnapped": unsnapped,
        "unreachablePairs": pairs,
        "unreachable": sorted(unreachable, key=lambda r: (-(r["unreachableTo"] + r["unreachableFrom"]), r["label"])),
    }


def region_points(region_id: str, depot: LatLon | None) -> tuple[list[Point], dict]:
    seed = load_region_seed(REFERENCE_REGIONS, REFERENCE_STATIONS, region_id)
    points = [Point(s.api_id, s.position) for s in seed.stations]
    points.append(Point("DEPOT", depot or seed.depot, depot=True))
    basis = "explicit --depot" if depot else "station-coordinate-mean; not an operational depot"
    return points, {"regionId": seed.region_id, "depotBasis": basis}


def mock_points(count: int, seed: int, depot: LatLon | None) -> tuple[list[Point], dict]:
    # Same generator and truncation as the 300-station benchmark (benchmark.build_case).
    stations, _ = make_stations(per_cluster=-(-count // 5), seed=seed)
    points = [Point(f"MOCK-{s.id}", (s.lat, s.lon)) for s in stations[:count]]
    points.append(Point("DEPOT", depot or DEPOT, depot=True))
    return points, {"mockStations": count, "seed": seed,
                    "depotBasis": "explicit --depot" if depot else "mock_data.DEPOT (synthetic)"}


def _depot(value: str) -> LatLon:
    try:
        lat, lon = (float(v) for v in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("--depot must be LAT,LON") from error
    return lat, lon


def parser():
    cli = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    target = cli.add_mutually_exclusive_group(required=True)
    target.add_argument("--region", help="reference region, e.g. N01")
    target.add_argument("--mock-stations", type=int, help="synthetic benchmark coordinates, e.g. 300")
    cli.add_argument("--seed", type=int, default=7, help="mock coordinate seed (benchmark default)")
    cli.add_argument("--depot", type=_depot, help="operational depot LAT,LON; default is the reference/mock depot")
    cli.add_argument("--osrm-url", required=True)
    cli.add_argument("--osrm-data-version", default="unversioned", help="expected road data version, recorded only")
    cli.add_argument("--osrm-max-locations", type=int, default=100)
    cli.add_argument("--osrm-timeout-seconds", type=float, default=10)
    cli.add_argument("--osrm-budget-seconds", type=float, default=120, help="diagnostics may exceed the solver budget")
    cli.add_argument("--osrm-snap-radius-metres", type=float, default=1000)
    return cli


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    road = OsrmTravelTime(args.osrm_url, data_version=args.osrm_data_version, max_locations=args.osrm_max_locations,
                          timeout_s=args.osrm_timeout_seconds, budget_s=args.osrm_budget_seconds,
                          snap_radius_m=args.osrm_snap_radius_metres)
    if args.region:
        points, scope = region_points(args.region, args.depot)
    else:
        if args.mock_stations < 1:
            parser().error("--mock-stations must be positive")
        points, scope = mock_points(args.mock_stations, args.seed, args.depot)
    try:
        report = check(points, road)
    except TravelTimeError as error:
        print(json.dumps({"ok": False, "error": error.code, "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    report = {"checkedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "osrmUrl": args.osrm_url, "expectedDataVersion": args.osrm_data_version, **scope, **report}
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
