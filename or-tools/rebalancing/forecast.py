# Author: Jung Minkyu
# Date: 20OCT2026
# Purpose: 수요 예측기의 인터페이스(DemandPredictor)와, 팀 AI 모델이 완성되기 전까지 쓰는 규칙 기반 mock 예측기를 정의한다.

"""수요 예측 계층.

팀의 실제 모델(예: 대여 이력과 날씨로 학습한 LSTM / XGBoost)은 `DemandPredictor`를
구현하는 방식으로 연결된다. `HeuristicPredictor`는 같은 인터페이스를 가진 mock이라서,
학습된 모델이 없어도 최적화 부분을 개발하고 테스트할 수 있다.
"""
from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Protocol

from .models import Forecast, Station
from .problem import require_int


class HourlyForecast(Protocol):
    """Structural interface shared with the data module's separate Forecast class."""

    @property
    def start(self) -> datetime: ...

    @property
    def rentals_h(self) -> Sequence[float]: ...

    @property
    def returns_h(self) -> Sequence[float]: ...

    @property
    def model_version(self) -> str: ...


class DemandPredictor(Protocol):
    """Date-aware predictors return hourly expectations without accessing business state."""

    def predict(self, station: Station, start: datetime, horizon_h: int) -> HourlyForecast: ...


def forecast_window(start: datetime, horizon_h: int) -> datetime:
    """Normalize aware timestamps to naive KST; never infer the date or round an hour."""
    require_int("horizon_h", horizon_h, minimum=1)
    if horizon_h > 5:
        raise ValueError("horizon_h must be in [1, 5]")
    if not isinstance(start, datetime):
        raise ValueError("forecast start must be an explicit datetime")
    try:
        if start.utcoffset() is not None:
            start = start.astimezone(timezone(timedelta(hours=9)))
        start = start.replace(tzinfo=None)
        if start.minute or start.second or start.microsecond:
            raise ValueError("forecast start must align to a whole KST hour")
        # Validate the entire requested window before calling a predictor.
        start + timedelta(hours=horizon_h)
    except OverflowError as error:
        raise ValueError("forecast window exceeds the datetime range") from error
    return start


# [mock 규칙] 출퇴근 패턴을 흉내 낸다.
# 업무지구는 아침(출근)에 자전거가 들어오고 저녁(퇴근)에 나간다.
# 주거지는 반대로 아침에 나가고 저녁에 들어온다.
_COMMUTE_IN = range(7, 10)  # 출근 시간대: 7, 8, 9시
_COMMUTE_OUT = range(17, 21)  # 퇴근 시간대: 17, 18, 19, 20시


class HeuristicPredictor:
    """규칙으로 숫자를 만들어 내는 가짜 예측기 (실제 AI 모델 아님)."""

    def __init__(self, kinds: dict[int, str], seed: int = 0):
        self._kinds = kinds  # 대여소 id -> 유형("business" / "residential" / "leisure")
        self._rng = random.Random(seed)  # seed가 같으면 항상 같은 숫자가 나온다(재현 가능)

    def predict(self, station: Station, start: datetime, horizon_h: int) -> Forecast:
        start = forecast_window(start, horizon_h)
        values = [self._one_hour(station, (start + timedelta(hours=i)).hour) for i in range(horizon_h)]
        return Forecast(start=start, rentals_h=tuple(r for r, _ in values),
                        returns_h=tuple(t for _, t in values), model_version="heuristic-v1")

    def _one_hour(self, station: Station, hour: int) -> tuple[int, int]:
        base = max(2, station.docks // 5)  # 평소(출퇴근이 아닐 때)의 대여·반납 수
        peak = station.docks // 2  # 출퇴근 때 한쪽으로 추가로 몰리는 양
        kind = self._kinds.get(station.id, "leisure")  # 유형을 모르면 평범한 곳(leisure)으로 본다

        rentals = returns = base
        if kind == "business":
            if hour in _COMMUTE_IN:
                returns += peak  # 업무지구 아침: 출근한 사람들이 자전거를 반납
            elif hour in _COMMUTE_OUT:
                rentals += peak  # 업무지구 저녁: 퇴근하는 사람들이 자전거를 대여
        elif kind == "residential":
            if hour in _COMMUTE_IN:
                rentals += peak  # 주거지 아침: 출근하는 사람들이 자전거를 대여
            elif hour in _COMMUTE_OUT:
                returns += peak  # 주거지 저녁: 퇴근한 사람들이 자전거를 반납

        # 실제 예측처럼 약간 틀리게 만들기 위해 -1~+1대의 무작위 오차를 더한다(0 미만은 0으로).
        jitter = lambda v: max(0, v + self._rng.randint(-1, 1))  # noqa: E731
        return jitter(rentals), jitter(returns)
