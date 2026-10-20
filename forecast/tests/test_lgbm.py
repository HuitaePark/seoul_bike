# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: 근무조 구간 순유입 채점과 '빌 대여소 맞히기' 채점이 정의대로 계산되는지 작은 가짜 자료로 확인한다.

import unittest

import pandas as pd

from forecast.lgbm import shift_mae, stockout


class ShiftMaeTest(unittest.TestCase):
    def test_sums_shift_hours_then_takes_absolute_error(self):
        h = pd.to_datetime(["2025-06-11 07:00", "2025-06-11 08:00", "2025-06-11 09:00", "2025-06-11 10:00"])
        df = pd.DataFrame({"st": 1, "h": h, "hh": h.hour, "rent": [1, 3, 0, 50], "ret": [2, 0, 1, 0],
                           "p_rent": [1, 1, 1, 0], "p_ret": [1, 1, 1, 0]})
        df["perfect_rent"], df["perfect_ret"] = df.rent, df.ret
        out = shift_mae(df, {"flat": ("p_ret", "p_rent"), "perfect": ("perfect_ret", "perfect_rent")})
        # 07~10시 구간의 실제 순유입은 1 − 3 + 1 = −1, flat 예측은 0 → 오차 1. 10시는 근무조 밖이라 빠진다
        self.assertEqual(out.loc["2025-06", "flat"], 1.0)
        self.assertEqual(out.loc["2025-06", "perfect"], 0.0)


class StockoutTest(unittest.TestCase):
    def test_finds_station_that_empties_from_start_stock(self):
        # 07~10시 근무조. A는 2대로 시작해 대여 1, 2 → 08시에 바닥을 넘는다. B는 5대로 시작해 1대만 빌린다
        h = pd.to_datetime(["2025-06-11 07:00", "2025-06-11 08:00", "2025-06-11 09:00"])
        rows = [dict(st=st, h=t, hh=t.hour, rent=r, ret=0) for st, rents in ((1, [1, 2, 0]), (2, [1, 0, 0])) for t, r in zip(h, rents)]
        df = pd.DataFrame(rows).assign(zero=0.0)
        df["good_rent"] = df.rent
        s0 = pd.Series([2, 5], index=pd.MultiIndex.from_tuples([(1, h[0]), (2, h[0])], names=["st", "h"]))
        out = stockout(df, s0, {"good": ("zero", "good_rent"), "flat": ("zero", "zero")}, top=1)
        self.assertEqual(out.loc["good", "빈 곳 중 잡은 비율"], 1.0)   # A를 잡는다
        self.assertEqual(out.loc["flat", "빈 곳 중 잡은 비율"], 0.0)   # 변화 없음으로 예측하면 못 잡는다
        self.assertEqual(out.loc["good", "근무조마다 위험 상위 1곳 적중"], 1.0)


if __name__ == "__main__":
    unittest.main()
