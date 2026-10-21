"""Hourly data predictor boundary, integer quantities, and unchanged one-hour mocks."""
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from rebalancing.forecast import HeuristicPredictor, forecast_window
from rebalancing.imbalance import compute_imbalances, healthy_band
from rebalancing.models import Forecast, Station

START = datetime(2025, 6, 16, 8)
STATION = Station(1, "A", 37.5, 127.0, capacity=10, bikes=2)
ROOT = Path(__file__).resolve().parents[2]


def prediction(rentals=(2.5,), returns=(1.5,)):
    return Forecast(START, rentals, returns, "data-test-v1")


def test_hourly_totals_and_model_provenance_are_preserved():
    forecast = Forecast(START, (1.25, 2.5), (0.5, 1.5), "model-2025-06")
    assert forecast.rentals_h == (1.25, 2.5) and forecast.returns_h == (0.5, 1.5)
    assert (forecast.rentals, forecast.returns, forecast.model_version) == (3.75, 2.0, "model-2025-06")


def test_heuristic_uses_each_hour_across_midnight_without_losing_date():
    start = datetime(2025, 6, 16, 23)
    station = replace(STATION, capacity=20)
    forecast = HeuristicPredictor({1: "residential"}, seed=7).predict(station, start, 3)
    sequential = HeuristicPredictor({1: "residential"}, seed=7)
    hours = [sequential.predict(station, start + timedelta(hours=i), 1) for i in range(3)]
    assert forecast.start == start
    assert forecast.rentals_h == tuple(f.rentals for f in hours)
    assert forecast.returns_h == tuple(f.returns for f in hours)
    assert len(forecast.rentals_h) == 3


def test_forecast_is_not_rounded_before_quantity_conversion():
    predictor = Mock()
    predictor.predict.return_value = prediction()
    result = compute_imbalances([STATION], predictor, START)
    # 2.5-1.5+2 = 3; rounding the two forecasts separately would hide this shortage.
    assert [(i.station_id, i.delta) for i in result] == [(1, -1)]
    assert predictor.predict.return_value.rentals_h == (2.5,)


@pytest.mark.parametrize("rentals,returns,expected", [
    (0.8, 0.2, (3, 10)), (0.2, 0.8, (2, 9)), (8, 2, (8, 16)),
    (30, 0, (32, 40)), (0, 30, (0, 0)),
])
def test_integer_band_protects_lower_and_upper_targets(rentals, returns, expected):
    assert healthy_band(STATION, rentals, returns, 2) == expected


def test_fractional_pickup_never_rounds_up_the_removable_quantity():
    predictor = Mock()
    predictor.predict.return_value = prediction((0.2,), (0.8,))
    result = compute_imbalances([replace(STATION, bikes=10)], predictor, START)
    assert [i.delta for i in result] == [1]


def test_decimal_hourly_sums_do_not_invent_an_extra_bicycle():
    predictor = Mock()
    predictor.predict.return_value = prediction((0.1, 0.2), (0.3, 0.0))
    assert compute_imbalances([STATION], predictor, START, horizon_h=2) == []


def test_operating_target_is_not_a_hard_dock_limit():
    station = replace(STATION, capacity=20, operating_limit=8)
    assert healthy_band(station, 2.5, 1.5, 2) == (3, 9)
    assert healthy_band(station, 2.5, 1.5, 2, operating_limit=0) == (3, 3)


@pytest.mark.parametrize("start", [START, START.replace(tzinfo=timezone(timedelta(hours=9))),
                                   datetime(2025, 6, 15, 23, tzinfo=timezone.utc)])
def test_kst_and_utc_call_the_data_predictor_with_the_same_local_window(start):
    predictor = Mock()
    predictor.predict.return_value = prediction()
    assert [i.delta for i in compute_imbalances([STATION], predictor, start)] == [-1]
    predictor.predict.assert_called_once_with(STATION, START, 1)


def test_forecast_start_may_have_an_equivalent_timezone():
    predictor = Mock()
    predictor.predict.return_value = replace(prediction(), start=datetime(2025, 6, 15, 23, tzinfo=timezone.utc))
    assert [i.delta for i in compute_imbalances([STATION], predictor, START)] == [-1]


@pytest.mark.parametrize("horizon", [0, -1, 6, True, 1.5, None])
def test_invalid_horizon_is_rejected_before_prediction(horizon):
    predictor = Mock()
    with pytest.raises(ValueError, match="horizon_h"):
        compute_imbalances([], predictor, START, horizon_h=horizon)
    predictor.predict.assert_not_called()


@pytest.mark.parametrize("start", [None, 8, date(2025, 6, 16), START.replace(minute=1),
                                    START.replace(second=1), START.replace(microsecond=1), datetime.max, datetime(9999, 12, 31, 23)])
def test_invalid_start_is_rejected_before_prediction(start):
    predictor = Mock()
    with pytest.raises(ValueError, match="forecast"):
        compute_imbalances([], predictor, start)
    predictor.predict.assert_not_called()


@pytest.mark.parametrize("patch", [
    {"start": START + timedelta(hours=1)}, {"model_version": ""}, {"model_version": None},
    {"rentals_h": ()}, {"returns_h": (1.0, 2.0)}, {"rentals_h": "2"},
    {"rentals_h": (-1.0,)}, {"rentals_h": (float("nan"),)},
    {"returns_h": (float("inf"),)}, {"returns_h": (True,)},
    {"rentals_h": (1_000_000_001,)}, {"returns_h": ("1",)},
])
def test_malformed_data_forecast_is_rejected(patch):
    predictor = Mock()
    predictor.predict.return_value = SimpleNamespace(**{**asdict(prediction()), **patch})
    with pytest.raises(ValueError):
        compute_imbalances([STATION], predictor, START)


def test_forecast_values_from_numpy_models_are_accepted_without_coercing_strings():
    import numpy as np
    predictor = Mock()
    predictor.predict.return_value = prediction((np.float64(2.5),), (np.float32(1.5),))
    assert [i.delta for i in compute_imbalances([STATION], predictor, START)] == [-1]


def test_forecast_total_cannot_exceed_supported_demand_range():
    predictor = Mock()
    predictor.predict.return_value = prediction((1e9, 1e9), (0.0, 0.0))
    with pytest.raises(ValueError, match="demand"):
        compute_imbalances([STATION], predictor, START, horizon_h=2)


def test_duplicate_stations_and_invalid_inventory_are_rejected():
    predictor = Mock()
    with pytest.raises(ValueError, match="duplicate"):
        compute_imbalances([STATION, STATION], predictor, START)
    with pytest.raises(ValueError, match="station.bikes"):
        compute_imbalances([replace(STATION, bikes=-1)], predictor, START)
    predictor.predict.assert_not_called()


def test_actual_data_baseline_connects_without_merging_forecast_classes(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    from forecast import Forecast as DataForecast
    from forecast.baseline import BaselinePredictor
    counts = {(1, START.date() - timedelta(days=i), 8): (10, 1) for i in range(1, 8)}
    predictor = BaselinePredictor(counts, set(), START.date(), weeks=1)
    raw = predictor.predict(STATION, START, 2)
    assert isinstance(raw, DataForecast) and not isinstance(raw, Forecast)
    result = compute_imbalances([replace(STATION, bikes=5)], predictor,
                                datetime(2025, 6, 15, 23, tzinfo=timezone.utc), horizon_h=2)
    assert [i.delta for i in result] == [-6]
    assert raw.model_version == "baseline-1w" and raw.rentals_h == (10.0, 0.0)


def test_endpoint_policy_does_not_claim_intermediate_trajectory_safety():
    predictor = Mock()
    # The end inventory recovers, though the first hour could empty the station.
    predictor.predict.return_value = prediction((10.0, 0.0), (0.0, 10.0))
    assert compute_imbalances([STATION], predictor, START, horizon_h=2) == []


def test_explicit_three_and_five_hour_windows_are_supported():
    for horizon in (3, 5):
        predictor = Mock()
        predictor.predict.return_value = prediction((2.0,) * horizon, (0.0,) * horizon)
        assert [i.delta for i in compute_imbalances([STATION], predictor, START, horizon_h=horizon)] == [-2 * horizon]


@pytest.mark.parametrize("args,expected_start,horizon", [
    (["--hour", "8"], "2026-10-20T08:00:00", 1),
    (["--start", "2025-06-15T23:00:00Z", "--horizon-h", "3"], "2025-06-16T08:00:00", 3),
])
def test_cli_keeps_hour_mode_and_accepts_explicit_forecast_windows(monkeypatch, capsys, args, expected_start, horizon):
    import json
    import sys
    from rebalancing.__main__ import main
    monkeypatch.setattr(sys, "argv", ["rebalancing", *args, "--json"])
    main()
    document = json.loads(capsys.readouterr().out)
    assert document["hour"] == 8
    assert document["settings"]["forecast_start_kst"] == expected_start
    assert document["settings"]["forecast_horizon_h"] == horizon
    assert len(document["plans"]) == 2


@pytest.mark.parametrize("args", [
    ["--horizon-h", "0"], ["--horizon-h", "6"], ["--start", "tomorrow"],
    ["--start", "2025-06-16"], ["--start", "2025-06-16T08:30:00+09:00"],
    ["--hour", "8", "--start", "2025-06-16T08:00:00+09:00"],
])
def test_cli_rejects_ambiguous_or_invalid_forecast_windows(monkeypatch, args):
    import sys
    from rebalancing.__main__ import main
    monkeypatch.setattr(sys, "argv", ["rebalancing", *args])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 2
