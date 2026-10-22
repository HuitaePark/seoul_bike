"""A supplied depot must be shared by coverage checks and PLAN/REPLAN measurements."""
from unittest.mock import patch

import pytest

from rebalancing import benchmark, roadcheck
from rebalancing.travel_time import HaversineTravelTime


@pytest.mark.parametrize("tool", [benchmark, roadcheck], ids=["benchmark", "roadcheck"])
@pytest.mark.parametrize("coordinate", ["nan,127", "37,inf", "91,127", "37,-181", "37", "37,127,1", "x,127"])
def test_bad_depot_is_a_cli_error_before_any_osrm_request(tool, coordinate, capsys):
    args = ["--depot=" + coordinate, "--osrm-url", "http://127.0.0.1:5000", "--osrm-data-version", "fixture-v1"]
    args += (["--travel-source", "osrm", "--stations", "5"] if tool is benchmark else ["--mock-stations", "5"])
    with patch("rebalancing.osrm.OsrmTravelTime._request", side_effect=AssertionError("invalid depot reached OSRM")) as request:
        with pytest.raises(SystemExit) as error:
            tool.main(args)
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert "--depot must be finite LAT,LON" in captured.err and not captured.out
    request.assert_not_called()


@pytest.mark.parametrize("coordinate,expected", [("0,0", (0.0, 0.0)), ("-90,-180", (-90.0, -180.0)), ("90,180", (90.0, 180.0))])
def test_both_tools_accept_the_same_zero_and_boundary_depots(coordinate, expected):
    measured = benchmark.parser().parse_args(["--depot=" + coordinate])
    checked = roadcheck.parser().parse_args(["--mock-stations", "5", "--osrm-url", "http://localhost:5000", "--depot=" + coordinate])
    assert measured.depot == checked.depot == expected
    assert roadcheck.mock_points(5, 7, checked.depot)[0][-1].position == expected


@pytest.mark.parametrize("geography", [["--stations", "10"], ["--region", "S09"]], ids=["mock", "reference"])
@pytest.mark.parametrize("scenario", ["plan", "replan"])
def test_explicit_depot_is_used_for_initial_routes_and_remaining_work(geography, scenario):
    class RecordingTravel:
        def __init__(self):
            self.points = []

        def matrix(self, points):
            self.points.append(list(points))
            return HaversineTravelTime().matrix(points)

    coordinate = (37.56, 126.98)
    args = benchmark.parser().parse_args([*geography, "--depot", "37.56,126.98", "--all-candidates",
                                          "--trucks", "3", "--scenario", scenario, "--solution-limit", "1"])
    travel = RecordingTravel()
    case = benchmark.build_case(args, travel)
    assert case.depot == coordinate
    if case.reference:
        assert case.reference["depot_basis"] == "explicit --depot"
    if scenario == "replan":
        assert case.completed and case.snapshot_elapsed_s > 0
        by_id = {station.id: (station.lat, station.lon) for station in case.stations}
        assert all(truck.position in {coordinate, *(by_id[i] for i in case.completed)} for truck in case.trucks)
        assert {truck.available_time_s for truck in case.trucks} == {10800 - case.snapshot_elapsed_s}
    result = case.solve(travel)
    assert result.routes and all(points[0] == coordinate for points in travel.points)
    # This also covers the initial solve used to construct a REPLAN snapshot.
    assert len(travel.points) == (2 if scenario == "replan" else 1)
    assert not {stop.station_id for route in result.routes for stop in route.stops} & case.completed
    args.depot = None
    default_case = benchmark.build_case(args, HaversineTravelTime())
    assert default_case.fingerprint() != case.fingerprint()


def test_depot_provenance_and_input_hash_are_preserved_across_cache_modes(capsys):
    base = ["--stations", "10", "--all-candidates", "--solution-limit", "1", "--repeats", "2", "--cache-mode", "compare"]
    default = benchmark.main(base)
    explicit_default = benchmark.main([*base, "--depot", ",".join(map(str, benchmark.DEPOT))])
    changed = benchmark.main([*base, "--depot", "37.56,126.98"])
    capsys.readouterr()
    assert default["depot_basis"] == "mock_data.DEPOT (synthetic)"
    assert default["input_sha256"] == explicit_default["input_sha256"] != changed["input_sha256"]
    assert default["depot"] == explicit_default["depot"]
    assert changed["depot"] == {"lat": 37.56, "lon": 126.98} and changed["depot_basis"] == "explicit --depot"
    assert changed["same_plan_across_cache_modes"]
    assert set(changed["cache_comparison"]) == {"none", "cold", "warm"}


@pytest.mark.parametrize("settings", [["--region", "N99"], ["--mock-stations", "0"],
                                      ["--region", "N02", "--osrm-budget-seconds", "nan"]])
def test_bad_roadcheck_setup_reports_a_cli_error_without_request(settings, capsys):
    with patch("rebalancing.osrm.OsrmTravelTime._request", side_effect=AssertionError("invalid settings reached OSRM")) as request:
        with pytest.raises(SystemExit) as error:
            roadcheck.main([*settings, "--osrm-url", "http://localhost:5000"])
    assert error.value.code == 2
    assert "Traceback" not in capsys.readouterr().err
    request.assert_not_called()
