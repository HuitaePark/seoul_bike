import json
from unittest.mock import patch

import pytest

from rebalancing.matrix_cache import CachedTravelTime
from rebalancing.osrm import OsrmTravelTime, TravelTimeError
from rebalancing.travel_time import TravelMatrix

POINTS = [(37, 127), (37.1, 127.1)]


class Provider:
    def __init__(self):
        self.calls = 0

    def matrix(self, points):
        self.calls += 1
        return TravelMatrix([[0 if a == b else 100 for b in points] for a in points],
                            [[0 if a == b else 10 for b in points] for a in points], "test")


def test_memory_cache_normalizes_numbers_and_isolates_mutation():
    provider = Provider()
    cache = CachedTravelTime(provider, namespace="map-v1")
    first = cache.matrix(POINTS)
    first.distances_m[0][1] = 999
    second = cache.matrix([(37.0, 127.0), POINTS[1]])
    assert second.distances_m[0][1] == 100 and provider.calls == 1
    second.durations_s[0][1] = 777
    assert cache.matrix(POINTS).durations_s[0][1] == 10
    assert (cache.hits, cache.misses) == (2, 1)


def test_disk_cache_survives_new_worker_and_version_change_invalidates(tmp_path):
    provider = Provider()
    CachedTravelTime(provider, namespace="map-v1", cache_dir=tmp_path).matrix(POINTS)
    CachedTravelTime(provider, namespace="map-v1", cache_dir=tmp_path).matrix(POINTS)
    assert provider.calls == 1
    CachedTravelTime(provider, namespace="map-v2", cache_dir=tmp_path).matrix(POINTS)
    assert provider.calls == 2


def test_position_and_order_invalidate_cache(tmp_path):
    provider = Provider()
    cache = CachedTravelTime(provider, namespace="v1", cache_dir=tmp_path)
    cache.matrix(POINTS)
    cache.matrix(list(reversed(POINTS)))
    cache.matrix([POINTS[0], (37.1000000001, 127.1)])
    assert provider.calls == 3


@pytest.mark.parametrize("field,value", [("base_url", "http://other"), ("profile", "truck"),
                                        ("data_version", "v2"), ("snap_radius_m", 100)])
def test_osrm_namespace_covers_provider_settings(field, value):
    original = OsrmTravelTime("http://localhost", data_version="v1")
    changed = OsrmTravelTime(**({"base_url": "http://localhost", "data_version": "v1"} | {field: value}))
    assert original.cache_namespace != changed.cache_namespace


def test_expired_memory_and_disk_entry_are_refetched(tmp_path):
    provider = Provider()
    cache = CachedTravelTime(provider, namespace="v1", cache_dir=tmp_path, ttl_s=1)
    with patch("rebalancing.matrix_cache.time", return_value=10):
        cache.matrix(POINTS)
    with patch("rebalancing.matrix_cache.time", return_value=12):
        cache.matrix(POINTS)
    with patch("rebalancing.matrix_cache.time", return_value=14):
        CachedTravelTime(provider, namespace="v1", cache_dir=tmp_path).matrix(POINTS)
    assert provider.calls == 3


@pytest.mark.parametrize("damage", ["json", "shape", "key", "expiry"])
def test_corrupt_disk_entry_is_recomputed(tmp_path, damage):
    provider = Provider()
    CachedTravelTime(provider, namespace="v1", cache_dir=tmp_path).matrix(POINTS)
    path = next(tmp_path.glob("*.json"))
    payload = json.loads(path.read_text())
    if damage == "shape":
        payload["matrix"]["distances_m"] = [[0]]
    elif damage == "key":
        payload["key"] = "wrong"
    elif damage == "expiry":
        payload["expires"] = "tomorrow"
    path.write_text("broken json" if damage == "json" else json.dumps(payload))
    result = CachedTravelTime(provider, namespace="v1", cache_dir=tmp_path).matrix(POINTS)
    assert provider.calls == 2 and result.distances_m[0][1] == 100


def test_lru_and_disk_growth_are_bounded(tmp_path):
    provider = Provider()
    cache = CachedTravelTime(provider, namespace="v1", cache_dir=tmp_path, max_entries=2)
    for i in range(4):
        cache.matrix([POINTS[0], (37 + i / 100, 127)])
    assert len(cache._memory) == 2 and len(list(tmp_path.glob("*.json"))) == 2
    cache.matrix([POINTS[0], (37, 127)])
    assert provider.calls == 5


def test_cache_io_failure_preserves_valid_provider_result(tmp_path, caplog):
    invalid_dir = tmp_path / "file"
    invalid_dir.write_text("not a directory")
    cache = CachedTravelTime(Provider(), namespace="v1", cache_dir=invalid_dir)
    assert cache.matrix(POINTS).durations_s[0][1] == 10
    assert "disk cache unavailable" in caplog.text


def test_failed_requests_are_never_cached(tmp_path):
    provider = Provider()
    cache = CachedTravelTime(provider, namespace="v1", cache_dir=tmp_path)
    with patch.object(provider, "matrix", side_effect=TravelTimeError("network_error", "offline")), \
            pytest.raises(TravelTimeError):
        cache.matrix(POINTS)
    assert list(tmp_path.iterdir()) == []
    assert cache.matrix(POINTS).durations_s[0][1] == 10
    assert provider.calls == 1
