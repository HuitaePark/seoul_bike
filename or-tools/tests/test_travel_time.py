"""Agreement 19: one estimate for distances, solver budgets and simulator legs."""
from math import ceil

import pytest

from rebalancing.geo import haversine_m
from rebalancing.travel_time import HaversineTravelTime

POINTS = [(37.5, 127.0), (37.501, 127.002), (37.5, 127.0)]


def test_default_estimate_scales_distance_and_time_once_and_keeps_zero_legs():
    provider = HaversineTravelTime()
    matrix = provider.matrix(POINTS)
    matrix.validate(len(POINTS))
    direct = haversine_m(*POINTS[0], *POINTS[1])
    assert (provider.speed_kph, provider.detour_factor) == (20, 1.3)
    assert matrix.distances_m[0][1] == ceil(direct * 1.3)
    assert matrix.durations_s[0][1] == ceil(direct * 1.3 / (20 / 3.6))
    assert matrix.distances_m[0][2] == matrix.durations_s[0][2] == 0
    assert matrix.distances_m[0][1] == matrix.distances_m[1][0]


def test_explicit_legacy_settings_and_cache_identity_are_kept_separate():
    agreed = HaversineTravelTime()
    legacy = HaversineTravelTime(speed_kph=30, detour_factor=1)
    slower = HaversineTravelTime(speed_kph=10, detour_factor=1.3)
    direct = haversine_m(*POINTS[0], *POINTS[1])
    assert legacy.matrix(POINTS).distances_m[0][1] == direct
    assert legacy.matrix(POINTS).durations_s[0][1] == ceil(direct / (30 / 3.6))
    assert len({p.cache_namespace for p in (agreed, legacy, slower)}) == 3
    assert agreed.cache_namespace == HaversineTravelTime(20, 1.3).cache_namespace
    assert agreed.cache_namespace != HaversineTravelTime(20.0000001, 1.3).cache_namespace


@pytest.mark.parametrize("kwargs", [
    {"speed_kph": 0}, {"speed_kph": -1}, {"speed_kph": float("nan")}, {"speed_kph": float("inf")},
    {"speed_kph": True}, {"speed_kph": "20"}, {"detour_factor": 0.9}, {"detour_factor": 0},
    {"detour_factor": float("nan")}, {"detour_factor": float("inf")}, {"detour_factor": True},
])
def test_invalid_estimates_are_rejected(kwargs):
    with pytest.raises(ValueError):
        HaversineTravelTime(**kwargs)


def test_empty_and_invalid_points_do_not_produce_fabricated_routes():
    assert HaversineTravelTime().matrix([]).distances_m == []
    with pytest.raises(ValueError, match="coordinates"):
        HaversineTravelTime().matrix([(float("nan"), 127)])
