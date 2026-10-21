"""Road coverage diagnostics against a fake OSRM; no downloaded map or routing server."""
import json
from urllib.parse import parse_qs, unquote, urlsplit
from unittest.mock import patch

import pytest

from rebalancing.osrm import OsrmTravelTime, TravelTimeError
from rebalancing.roadcheck import Point, check, main, mock_points, region_points

FAR = (37.9, 127.9)        # No road within the radius.
ISLAND = (37.2, 127.2)     # Snaps, but cannot leave or be reached.
ONE_WAY = (37.3, 127.3)    # Reachable from others, cannot drive back out.


def fake_osrm(url, timeout):
    parsed = urlsplit(url)
    path = unquote(parsed.path)
    if "/nearest/" in path:
        lon, lat = (float(v) for v in path.rsplit("/", 1)[1].split(","))
        if (lat, lon) == FAR:
            return {"code": "Ok", "waypoints": [{"distance": 4200.0}]}
        return {"code": "Ok", "waypoints": [{"distance": 12.5}]}
    coords = [tuple(reversed([float(v) for v in p.split(",")])) for p in path.split("/driving/")[1].split(";")]
    query = parse_qs(parsed.query)
    src = [int(v) for v in query["sources"][0].split(";")]
    dst = [int(v) for v in query["destinations"][0].split(";")]

    def cell(a, b):
        if a == b:
            return 0
        if ISLAND in (a, b) or a == ONE_WAY:
            return None
        return 100
    values = [[cell(coords[i], coords[j]) for j in dst] for i in src]
    return {"code": "Ok", "data_version": "fixture-map", "distances": values, "durations": values}


def run(points, **kwargs):
    with patch.object(OsrmTravelTime, "_request", side_effect=fake_osrm):
        return check(points, OsrmTravelTime("http://localhost:5000", **kwargs))


def test_clean_points_pass_and_shared_coordinates_are_queried_once():
    points = [Point("ST-1", (37.0, 127.0)), Point("ST-2", (37.0, 127.0)), Point("DEPOT", (37.1, 127.1), depot=True)]
    report = run(points)
    assert report["ok"] and report["distinctSnappedCoordinates"] == 2
    assert report["observedDataVersion"] == "fixture-map" and report["unreachablePairs"] == 0


def test_lists_unsnapped_islands_and_one_way_points_with_depot_direction():
    points = [Point("ST-1", (37.0, 127.0)), Point("ST-FAR", FAR), Point("ST-ISLAND", ISLAND),
              Point("ST-ONEWAY", ONE_WAY), Point("DEPOT", (37.1, 127.1), depot=True)]
    report = run(points)
    assert not report["ok"]
    assert report["unsnapped"] == [{"label": "ST-FAR", "lat": FAR[0], "lon": FAR[1], "nearestRoadM": 4200.0}]
    rows = {r["label"]: r for r in report["unreachable"]}
    assert rows["ST-ISLAND"]["unreachableTo"] == rows["ST-ISLAND"]["unreachableFrom"] == 3
    assert rows["ST-ISLAND"]["depotToStationMissing"] and rows["ST-ISLAND"]["stationToDepotMissing"]
    assert rows["ST-ONEWAY"]["stationToDepotMissing"] and not rows["ST-ONEWAY"]["depotToStationMissing"]
    # Points that only fail towards the island/one-way are still listed with their counts.
    assert rows["ST-1"]["unreachableTo"] == 1 and rows["ST-1"]["unreachableFrom"] == 2
    assert report["unreachable"][0]["label"] == "ST-ISLAND"


def test_solver_matrix_still_rejects_the_first_unreachable_cell():
    with patch.object(OsrmTravelTime, "_request", side_effect=fake_osrm):
        with pytest.raises(TravelTimeError) as caught:
            OsrmTravelTime("http://localhost:5000").matrix([(37.0, 127.0), ISLAND])
    assert caught.value.code == "unreachable"


def test_invalid_nearest_distance_is_not_treated_as_a_road():
    with patch.object(OsrmTravelTime, "_request", return_value={"code": "Ok", "waypoints": [{"distance": -1}]}):
        with pytest.raises(TravelTimeError, match="nearest"):
            OsrmTravelTime("http://localhost:5000").nearest_distance((37.0, 127.0))


def test_reachability_requires_distinct_points():
    with pytest.raises(ValueError, match="distinct"):
        OsrmTravelTime("http://localhost:5000").reachability([(37.0, 127.0), (37.0, 127.0)])


def test_region_and_mock_scopes_label_their_depot_basis():
    points, scope = region_points("N01", None)
    assert len(points) == 266 and points[-1].depot and "not an operational depot" in scope["depotBasis"]
    points, scope = region_points("N01", (37.6, 127.0))
    assert points[-1].position == (37.6, 127.0) and scope["depotBasis"] == "explicit --depot"
    points, scope = mock_points(300, 7, None)
    assert len(points) == 301 and len({p.label for p in points}) == 301


def test_cli_exit_code_reflects_coverage(capsys):
    with patch.object(OsrmTravelTime, "_request", side_effect=fake_osrm):
        assert main(["--region", "N02", "--osrm-url", "http://localhost:5000", "--osrm-data-version", "v1"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["regionId"] == "N02" and report["expectedDataVersion"] == "v1" and report["ok"]
    with patch.object(OsrmTravelTime, "_request", side_effect=fake_osrm):
        assert main(["--mock-stations", "3", "--depot", f"{FAR[0]},{FAR[1]}", "--osrm-url", "http://localhost:5000"]) == 1
    assert json.loads(capsys.readouterr().out)["unsnapped"][0]["label"] == "DEPOT"


@pytest.mark.parametrize("geography", [["--region", "N02"], ["--mock-stations", "5"]])
def test_cli_records_explicit_depot_for_reproducible_measurement(geography, capsys):
    with patch.object(OsrmTravelTime, "_request", side_effect=fake_osrm):
        assert main([*geography, "--depot", "37.56,126.98", "--osrm-url", "http://localhost:5000"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["depot"] == {"lat": 37.56, "lon": 126.98} and report["depotBasis"] == "explicit --depot"
