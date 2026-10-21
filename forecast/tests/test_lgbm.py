# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: 근무조 구간 순유입 채점과 '빌 대여소 맞히기' 채점이 정의대로 계산되는지 작은 가짜 자료로 확인한다.

import unittest

import pandas as pd

from forecast.lgbm import FEATS, shift_mae, stockout
from forecast.nowcast import stack, with_events, with_lags


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


class NowcastStackTest(unittest.TestCase):
    def test_recent_features_end_k_hours_before_target(self):
        # 대여소 하나, 대여 수 = 그 시각의 순번(0, 1, 2, …). lead k에서 '직전 1시간'은 대상 시각 k시간 전이어야 한다(미래를 보지 않음)
        h = pd.date_range("2025-06-11 00:00", periods=12, freq="h")
        df = pd.DataFrame({"st": 1, "h": h, "rent": range(12), "ret": 0, "empty_min": 0.0, "rain_mm": 0.0,
                           "plain_rent": 1.0, "base_ret": 0.0, **{f: 0.0 for f in FEATS if f not in ("plain_rent", "base_ret")}})
        df["hh"] = df.h.dt.hour
        fw = pd.DataFrame({"h": [], "lead": []}).astype({"h": "datetime64[ns]", "lead": "int64"})
        s = stack(with_lags(df), fw, 1.0)
        row = s[(s.h == h[10]) & (s.lead == 3)].iloc[0]
        self.assertEqual(row.rent_rec1, 7)            # 10시 대상, lead 3 → 예측 시점 08시, 직전 1시간은 07시
        self.assertEqual(row.rent_rec3, 7 + 6 + 5)    # 07·06·05시
        self.assertEqual(row.zone_ratio3, 18 / 3)     # 구역(대여소 하나) 대여 18 ÷ 베이스라인 3


if __name__ == "__main__":
    unittest.main()


class EventFeaturesTest(unittest.TestCase):
    def test_marks_exit_hours_only_near_venue_on_event_day(self):
        # 186번은 상암 700m 안. 2025-06-10 한국-쿠웨이트(20시 시작, 2시간): 도착 16~19시, 퇴장 22~23시
        h = pd.to_datetime(["2025-06-10 17:00", "2025-06-10 22:00", "2025-06-11 22:00", "2025-06-10 22:00"])
        out = with_events(pd.DataFrame({"st": [186, 186, 186, 99999], "h": h, "hh": h.hour}))
        self.assertEqual(out.ev_arrive.tolist(), [1, 0, 0, 0])
        self.assertEqual(out.ev_exit.tolist(), [0, 1, 0, 0])
        self.assertEqual(out.ev_type.tolist(), [1, 1, 0, 0])

    def test_two_events_at_one_station_keep_one_row(self):
        # 같은 날 가까운 두 행사장(야구 18시, 농구 14시)에 걸린 대여소: 행이 늘지 않고, 시각마다 어느 쪽이든 해당하면 1
        ev = pd.DataFrame({"st_no": [7, 7], "date": ["2025-03-29"] * 2, "type": [2, 8], "arrive_h0": [14, 10], "arrive_h1": [18, 14],
                           "exit_h0": [21, 16], "exit_h1": [23, 18], "closed": [0, 0]})
        h = pd.to_datetime(["2025-03-29 11:00", "2025-03-29 16:00", "2025-03-29 22:00"])
        out = with_events(pd.DataFrame({"st": [7, 7, 7], "h": h, "hh": h.hour}), ev)
        self.assertEqual(len(out), 3)
        self.assertEqual(out.ev_arrive.tolist(), [1, 1, 0])
        self.assertEqual(out.ev_exit.tolist(), [0, 1, 1])
