"""Road-matrix behavior without a public routing service or downloaded map."""
import json
import math
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from threading import Thread
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlsplit
from unittest.mock import patch

import pytest

from rebalancing.osrm import OsrmTravelTime, TravelTimeError

POINTS = [(37.0, 127.0), (37.1, 127.1), (37.0, 127.0)]


def road_cost(a, b):
    return 0 if a == b else abs(a[0] - b[0]) * 10000 + (70.2 if a > b else 30.2)


def table_payload(url):
    parsed = urlsplit(url)
    coords = [tuple(reversed([float(v) for v in point.split(",")]))
              for point in unquote(parsed.path).split("/driving/")[1].split(";")]
    query = parse_qs(parsed.query)
    assert query["annotations"] == ["distance,duration"]
    assert len(query["radiuses"][0].split(";")) == len(coords)
    assert all(float(v) == 1000 for v in query["radiuses"][0].split(";"))
    src = [int(v) for v in query["sources"][0].split(";")]
    dst = [int(v) for v in query["destinations"][0].split(";")]
    values = [[road_cost(coords[i], coords[j]) for j in dst] for i in src]
    return {"code": "Ok", "data_version": "fixture-map", "distances": values,
            "durations": [[v / 10 for v in row] for row in values]}, coords


def test_coordinate_order_duplicates_rounding_and_direction():
    seen = []

    def request(url, timeout):
        payload, coords = table_payload(url)
        seen.append(coords)
        return payload

    with patch.object(OsrmTravelTime, "_request", side_effect=request):
        matrix = OsrmTravelTime("http://localhost:5000").matrix(POINTS)
    assert seen == [POINTS[:2]]  # Lat/lon reconstructed from the wire's lon/lat.
    assert matrix.distances_m == [[math.ceil(road_cost(a, b)) for b in POINTS] for a in POINTS]
    assert matrix.durations_s == [[math.ceil(road_cost(a, b) / 10) for b in POINTS] for a in POINTS]
    assert matrix.distances_m[0][1] != matrix.distances_m[1][0]
    assert matrix.source == "osrm"


def test_200_stations_split_without_losing_order_or_directed_cells():
    points = [(37 + i / 1000, 127.0) for i in range(201)]
    points += [points[0]] * 5  # Depot plus current truck positions.
    calls = []

    def request(url, timeout):
        payload, coords = table_payload(url)
        calls.append(len(coords))
        return payload

    with patch.object(OsrmTravelTime, "_request", side_effect=request):
        matrix = OsrmTravelTime("http://localhost", max_locations=100).matrix(points)
    assert len(calls) == 25 and max(calls) <= 100
    assert matrix.distances_m == [[math.ceil(road_cost(a, b)) for b in points] for a in points]
    assert matrix.durations_s == [[math.ceil(road_cost(a, b) / 10) for b in points] for a in points]
    matrix.validate(206)


def test_empty_input_does_not_use_http():
    with patch.object(OsrmTravelTime, "_request") as request:
        result = OsrmTravelTime("http://localhost").matrix([])
    assert result.durations_s == [] and result.distances_m == []
    request.assert_not_called()


@pytest.mark.parametrize("value", [None, -1, float("nan"), float("inf"), True, "3", 1e10])
def test_bad_cells_reject_the_entire_matrix(value):
    payload = {"code": "Ok", "distances": [[0, value], [10, 0]], "durations": [[0, 1], [1, 0]]}
    with patch.object(OsrmTravelTime, "_request", return_value=payload), pytest.raises(TravelTimeError) as caught:
        OsrmTravelTime("http://localhost").matrix(POINTS[:2])
    assert caught.value.code == ("unreachable" if value is None else "invalid_response")
    assert not caught.value.retryable


@pytest.mark.parametrize("values", [[], [[0]], [[1, 1], [1, 0]]])
def test_bad_dimensions_and_nonzero_diagonal(values):
    payload = {"code": "Ok", "distances": values, "durations": [[0, 1], [1, 0]]}
    with patch.object(OsrmTravelTime, "_request", return_value=payload), pytest.raises(TravelTimeError):
        OsrmTravelTime("http://localhost").matrix(POINTS[:2])


@pytest.mark.parametrize("payload", [b"not json", b"[]", b'{"code":"TooBig"}',
                                     b'{"code":"Ok","fallback_speed_cells":[[0,1]]}'])
def test_protocol_errors_are_explicit(payload):
    with patch("rebalancing.osrm.urlopen", return_value=BytesIO(payload)), pytest.raises(TravelTimeError):
        OsrmTravelTime("http://localhost").matrix(POINTS[:2])


@pytest.mark.parametrize("error,retryable", [(TimeoutError(), True), (URLError(TimeoutError()), True), (URLError("offline"), True),
    (HTTPError("http://localhost", 503, "unavailable", {}, None), True),
    (HTTPError("http://localhost", 429, "limit", {}, None), True),
    (HTTPError("http://localhost", 400, "invalid", {}, None), False)])
def test_transport_failure_no_retry_no_estimate(error, retryable):
    with patch("rebalancing.osrm.urlopen", side_effect=error) as request, pytest.raises(TravelTimeError) as caught:
        OsrmTravelTime("http://localhost").matrix(POINTS[:2])
    assert caught.value.retryable is retryable
    if isinstance(error, TimeoutError) or isinstance(getattr(error, "reason", None), TimeoutError):
        assert caught.value.code == "timeout"
    assert request.call_count == 1


def test_budget_stops_before_another_block():
    with patch("rebalancing.osrm.monotonic", side_effect=[0, 0, 1, 9]), \
            patch.object(OsrmTravelTime, "_request", side_effect=lambda u, t: table_payload(u)[0]) as request, \
            pytest.raises(TravelTimeError) as caught:
        OsrmTravelTime("http://localhost", max_locations=2, budget_s=8).matrix(POINTS[:2] + [(37.2, 127)])
    assert caught.value.code == "budget_exceeded" and request.call_count == 1


def test_road_update_during_chunking_is_rejected():
    calls = 0

    def request(url, timeout):
        nonlocal calls
        calls += 1
        payload, _ = table_payload(url)
        payload["data_version"] = str(calls)
        return payload

    with patch.object(OsrmTravelTime, "_request", side_effect=request), pytest.raises(TravelTimeError) as caught:
        OsrmTravelTime("http://localhost", max_locations=2).matrix(POINTS[:2] + [(37.2, 127)])
    assert caught.value.code == "data_changed"


@pytest.mark.parametrize("kwargs", [{"base_url": "ftp://host"}, {"base_url": "http://user:pass@host"},
    {"base_url": "http://host?x=y"}, {"profile": "../car"}, {"max_locations": 1},
    {"timeout_s": 0}, {"budget_s": float("nan")}, {"data_version": ""}, {"snap_radius_m": -1}])
def test_bad_provider_options(kwargs):
    with pytest.raises(ValueError):
        OsrmTravelTime(**({"base_url": "http://localhost"} | kwargs))


def test_invalid_coordinates_fail_before_http():
    with patch.object(OsrmTravelTime, "_request") as request, pytest.raises(ValueError):
        OsrmTravelTime("http://localhost").matrix([(127, 37)])
    request.assert_not_called()


def test_real_http_cli_and_disk_cache(tmp_path):
    """Exercise actual urllib, both solvers, and reuse in a separate process."""
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            payload, _ = table_payload(self.path)
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    command = [sys.executable, "-B", "-m", "rebalancing", "--osrm-url",
               f"http://127.0.0.1:{server.server_port}", "--osrm-data-version", "fixture-v1",
               "--matrix-cache-dir", str(tmp_path), "--json"]
    try:
        first = json.loads(subprocess.run(command, check=True, capture_output=True, text=True).stdout)
        first_requests = len(requests)
        second = json.loads(subprocess.run(command, check=True, capture_output=True, text=True).stdout)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert first_requests == 1 and len(requests) == 1
    assert first["plans"] == second["plans"]
    assert first["settings"]["cache_hits"] == 1 and first["settings"]["cache_misses"] == 1
    assert second["settings"]["cache_hits"] == 2 and second["settings"]["cache_misses"] == 0


def test_cli_requires_road_version_for_disk_cache(tmp_path):
    result = subprocess.run([sys.executable, "-B", "-m", "rebalancing", "--osrm-url", "http://localhost",
                             "--matrix-cache-dir", str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 2 and "explicit --osrm-data-version" in result.stderr
