"""Connect cached data-model outputs to role 5 without training or real data files."""
from datetime import datetime, timezone
from unittest.mock import Mock

import numpy as np
import pandas as pd

from forecast.lgbm import FEATS, LightGBMPredictor
from forecast.nowcast import FEATS_N, NowcastPredictor
from rebalancing.imbalance import compute_imbalances
from rebalancing.models import Station

START = datetime(2025, 6, 16, 8)
STATION = Station(1, "A", 37.5, 127.0, 10, 2)


def cached_models(rentals, returns):
    rent, ret = Mock(), Mock()
    rent.predict.return_value = np.array(rentals, dtype=np.float64)
    ret.predict.return_value = np.array(returns, dtype=np.float64)
    return rent, ret


def test_lightgbm_cached_hourly_outputs_connect_without_rounding_each_forecast():
    frame = pd.DataFrame({name: [0.0, 0.0] for name in FEATS})
    frame["st"] = [1, 1]
    frame["h"] = pd.date_range(START, periods=2, freq="h")
    rent, ret = cached_models([2.5, 0.25], [1.5, 0.75])
    predictor = LightGBMPredictor(rent, ret, frame, "lgb-cache-test")
    raw = predictor.predict(STATION, START, 2)
    assert raw.model_version == "lgb-cache-test" and raw.rentals_h == (2.5, 0.25)
    assert [i.delta for i in compute_imbalances([STATION], predictor, START, horizon_h=2)] == [-1]
    # Cached predictor lookups do not rerun model inference during quantity conversion.
    assert rent.predict.call_count == ret.predict.call_count == 1


def test_nowcast_uses_prediction_lead_and_normalized_kst_keys():
    frame = pd.DataFrame({name: [0.0] * 3 for name in FEATS_N})
    frame["st"] = [1, 1, 1]
    frame["h"] = [pd.Timestamp(START), pd.Timestamp(START).replace(hour=9), pd.Timestamp(START).replace(hour=9)]
    frame["lead"] = [1, 1, 2]
    # The 09:00 lead-1 prediction must not leak into the earlier 08:00 forecast window.
    rent, ret = cached_models([2.5, 100.0, 0.25], [1.5, 0.0, 0.75])
    predictor = NowcastPredictor(rent, ret, frame, "nowcast-cache-test")
    raw = predictor.predict(STATION, START, 2)
    assert raw.rentals_h == (2.5, 0.25) and raw.model_version == "nowcast-cache-test"
    utc_start = datetime(2025, 6, 15, 23, tzinfo=timezone.utc)
    assert [i.delta for i in compute_imbalances([STATION], predictor, utc_start, horizon_h=2)] == [-1]
    assert rent.predict.call_count == ret.predict.call_count == 1
