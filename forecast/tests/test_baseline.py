# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: 베이스라인이 같은 요일 유형만 평균하고, 공휴일을 주말로 보고, 예측하는 날 자료를 쓰지 않는지 확인한다.

import unittest
from datetime import date, datetime, timedelta

from forecast.baseline import BaselinePredictor
from rebalancing.models import Station

S = Station(1, "A", 37.5, 127.0, 10, 5)
UNTIL = date(2025, 6, 16)  # 월요일
HOLIDAY = date(2025, 6, 6)  # 현충일(금)


def counts():
    """앞선 5주 동안 08시 대여: 평일 10건, 주말·공휴일 2건(토요일 6/14 예측의 4주 창까지 덮게).
    예측하는 날(6/16) 08시에는 99건을 넣어 두고 안 쓰는지 본다."""
    c = {}
    for k in range(1, 36):
        d = UNTIL - timedelta(days=k)
        c[(1, d, 8)] = (2, 0) if d.weekday() >= 5 or d == HOLIDAY else (10, 1)
    c[(1, UNTIL, 8)] = (99, 99)
    return c


class BaselineTest(unittest.TestCase):
    def setUp(self):
        self.p = BaselinePredictor(counts(), {HOLIDAY}, UNTIL)

    def test_weekday_uses_only_past_weekdays(self):
        f = self.p.predict(S, datetime(2025, 6, 16, 8), horizon_h=2)
        self.assertEqual(f.rentals_h, (10.0, 0.0))  # 08시는 평일 평균, 09시는 기록 없음
        self.assertEqual(f.returns, 1.0)

    def test_weekend_and_holiday_share_a_type(self):
        f = self.p.predict(S, datetime(2025, 6, 14, 8), horizon_h=1)  # 토요일: 앞선 4주의 주말·공휴일만
        self.assertEqual(f.rentals_h, (2.0,))

    def test_refuses_days_after_until(self):
        with self.assertRaises(AssertionError):
            self.p.predict(S, datetime(2025, 6, 17, 8), horizon_h=1)


if __name__ == "__main__":
    unittest.main()
