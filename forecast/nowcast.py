# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: 예측 시점 기준 LightGBM. 1~5시간 뒤 대여·반납을 오늘 들어온 정보와 예보 날씨로 예측하고, 지금 모델·베이스라인과 비교한다.

"""예측 시점 기준 LightGBM (PRD 7.3, 합의 29번: 최대 5시간).

예측 시점 t(정시)에 t시 ~ t+4시를 예측한다. lead k = 1..5, 대상 시각 h = t + k − 1. h − k시까지 끝난 자료만 쓴다.
lgbm.py와 달리 그 시각의 관측 날씨를 쓰지 않는다(운영할 때는 알 수 없다). 대신:
  예보 날씨  t보다 1시간 이상 앞서 발표된 최신 단기예보의 기온·1시간 강수량·강수확률 (analysis/weather_fcst.py)
  오늘 정보  t 직전 1·3시간의 대여소별 대여·반납, 같은 시간 베이스라인 기대값, 구역 전체가 평소의 몇 배였는지
비교: 베이스라인 4주 / lgbm.py 모형(관측 날씨, 참고용) / 예보 날씨만 / 예보 날씨 + 오늘 정보.
학습은 lead마다 행의 SAMPLE만 쓴다(5배로 쌓이므로). 검증은 모두 쓴다. 기간은 lgbm.py와 같다.
실행: .venv/bin/python -m forecast.nowcast --terr 강남-05   (약 10분)
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd

from rebalancing.models import Forecast, Station

from .lgbm import EMPTY_MAX, FEATS, ROOT, SHIFTS, features, fit, shift_mae, shift_start_stock, stockout, zone_wape

LEADS = range(1, 6)
SAMPLE = 0.3
OBSERVED = ["temp", "rain_mm", "rain_prev3h"]
FEATS_F = [f for f in FEATS if f not in OBSERVED] + ["lead", "f_temp", "f_pcp", "f_pop"]
FEATS_N = FEATS_F + ["rent_rec1", "rent_rec3", "ret_rec1", "ret_rec3", "exp_rent3", "exp_ret3", "zone_ratio3"]


def with_lags(df: pd.DataFrame) -> pd.DataFrame:
    """대여소·구역의 1~7시간 전 값(대여, 반납, 베이스라인 기대값)을 붙인다. df는 대여소마다 빈틈없는 시각 격자다."""
    df = df.sort_values(["st", "h"]).reset_index(drop=True)
    g = df.groupby("st")
    for i in range(1, 8):
        for c in ("rent", "ret", "plain_rent", "base_ret"):
            df[f"{c}_l{i}"] = g[c].shift(i).astype("float32")
    z = df.groupby("h")[["rent", "plain_rent"]].sum()
    for i in range(1, 8):
        zi = z.shift(i)
        df[f"zr_l{i}"] = df.h.map(zi.rent).astype("float32")
        df[f"zp_l{i}"] = df.h.map(zi.plain_rent).astype("float32")
    return df


def forecast_weather(hours: pd.Series) -> pd.DataFrame:
    """(대상 시각 h, lead k)마다 h − k시보다 늦지 않게 발표된(= 예측 시점보다 1시간 이상 앞선) 최신 예보."""
    c = duckdb.connect()
    c.execute("CREATE TABLE q AS SELECT h, k, h - k * INTERVAL 1 HOUR AS cut FROM (SELECT unnest($h) AS h), (SELECT unnest($k) AS k)",
              {"h": sorted(hours.drop_duplicates().dt.to_pydatetime()), "k": list(LEADS)})
    return c.execute(f"""SELECT q.h, q.k AS lead, f.temp AS f_temp, f.pcp AS f_pcp, f.pop AS f_pop
                         FROM q ASOF LEFT JOIN '{ROOT}/data/shared/weather_fcst.parquet' f ON f.target = q.h AND f.issue <= q.cut""").df()


def stack(df: pd.DataFrame, fw: pd.DataFrame, frac: float, seed: int = 0) -> pd.DataFrame:
    """lead마다 행을 복사해(학습은 frac만) 그 lead에서 볼 수 있는 오늘 정보와 예보를 붙인다."""
    parts = []
    for k in LEADS:
        d = df.sample(frac=frac, random_state=seed + k) if frac < 1 else df
        lag = lambda c, i: d[f"{c}_l{i}"]  # noqa: E731
        # rain_mm은 채점에서 비 온 시각을 나누는 데만 쓴다(피처 아님)
        p = d[["st", "h", "hh", "rent", "ret", "empty_min", "rain_mm", "plain_rent", "base_ret", *[f for f in FEATS if f not in OBSERVED]]].copy()
        p = p.loc[:, ~p.columns.duplicated()]
        p["lead"] = k
        p["rent_rec1"], p["ret_rec1"] = lag("rent", k), lag("ret", k)
        p["rent_rec3"] = lag("rent", k) + lag("rent", k + 1) + lag("rent", k + 2)
        p["ret_rec3"] = lag("ret", k) + lag("ret", k + 1) + lag("ret", k + 2)
        p["exp_rent3"] = lag("plain_rent", k) + lag("plain_rent", k + 1) + lag("plain_rent", k + 2)
        p["exp_ret3"] = lag("base_ret", k) + lag("base_ret", k + 1) + lag("base_ret", k + 2)
        p["zone_ratio3"] = (lag("zr", k) + lag("zr", k + 1) + lag("zr", k + 2)) / (lag("zp", k) + lag("zp", k + 1) + lag("zp", k + 2))
        parts.append(p)
    return pd.concat(parts, ignore_index=True).merge(fw, on=["h", "lead"], how="left")


class NowcastPredictor:
    """DemandPredictor 구현. 예측 시점 start에서 i시간 뒤는 lead i+1의 예측을 쓴다. 미리 만든 표의 시각만 예측할 수 있다."""

    def __init__(self, rent: lgb.Booster, ret: lgb.Booster, frame: pd.DataFrame, version: str):
        self.version = version
        self.pred = dict(zip(zip(frame.st, frame.h, frame.lead), zip(rent.predict(frame[FEATS_N]), ret.predict(frame[FEATS_N]))))

    def predict(self, station: Station, start: datetime, horizon_h: int) -> Forecast:
        assert horizon_h <= max(LEADS), f"최대 {max(LEADS)}시간까지 예측한다"
        vals = [self.pred[(station.id, start + timedelta(hours=i), i + 1)] for i in range(horizon_h)]
        return Forecast(start=start, rentals_h=tuple(v[0] for v in vals), returns_h=tuple(v[1] for v in vals), model_version=self.version)


def at_shift_start(s: pd.DataFrame) -> pd.DataFrame:
    """근무조 시작에 예측했다고 보고, 근무조 각 시각을 그 시각까지의 lead(1, 2, 3)로 고른다."""
    lead = {h: h - h0 + 1 for h0, h1 in SHIFTS for h in range(h0, h1)}
    return s[s.hh.map(lead) == s.lead]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--terr", default="강남-05")
    args = ap.parse_args()

    df = with_lags(features(args.terr, date(2024, 1, 31), date(2025, 7, 1)))
    df = df[df.h >= "2024-02-01"]
    fw = forecast_weather(df.h)
    tr = df[df.h < "2025-03-01"]
    stop, valid = df[(df.h >= "2025-03-01") & (df.h < "2025-04-01")], df[df.h >= "2025-04-01"]
    s_tr, s_stop, s_val = stack(tr, fw, SAMPLE), stack(stop, fw, SAMPLE), stack(valid, fw, 1.0)
    ok = lambda x: x[x.empty_min < EMPTY_MAX]  # noqa: E731
    print(f"{args.terr}  학습 {len(s_tr):,}행(lead 5개 × {SAMPLE:.0%})  검증 {len(s_val):,}행  예보 없는 행 {s_val.f_temp.isna().mean():.1%}")

    old = {n: lgb.Booster(model_file=str(ROOT / f"data/models/lgbm_{args.terr}_{n}.txt")) for n in ("rent", "ret")}
    obs = valid.set_index(["st", "h"])[FEATS]
    s_val = s_val.join(pd.DataFrame({"obs_rent": old["rent"].predict(obs), "obs_ret": old["ret"].predict(obs)}, index=obs.index), on=["st", "h"])
    for name, feats in (("fc", FEATS_F), ("now", FEATS_N)):
        m_rent, m_ret = fit(ok(s_tr), ok(s_stop), "rent", feats), fit(s_tr, s_stop, "ret", feats)
        s_val[f"{name}_rent"], s_val[f"{name}_ret"] = m_rent.predict(s_val[feats]), m_ret.predict(s_val[feats])
        print(f"  {name}: 나무 수 대여 {m_rent.best_iteration} · 반납 {m_ret.best_iteration}")
        if name == "now":
            out = ROOT / "data/models"
            m_rent.save_model(str(out / f"nowcast_{args.terr}_rent.txt"))
            m_ret.save_model(str(out / f"nowcast_{args.terr}_ret.txt"))
            imp = pd.Series(m_rent.feature_importance("gain"), index=feats).sort_values(ascending=False)

    names = {"베이스라인 4주": ("base_ret", "plain_rent"), "관측 날씨(참고)": ("obs_ret", "obs_rent"),
             "예보 날씨": ("fc_ret", "fc_rent"), "예보 + 오늘 정보": ("now_ret", "now_rent")}
    sv = at_shift_start(s_val)
    print("\n== 근무조 시작에 예측: 근무조에 빌 대여소 맞히기")
    print(stockout(sv, shift_start_stock(sv), names).to_string())
    print("\n== 근무조 시작에 예측: 구역 전체 시간별 대여 수 가중 오차율")
    print(zone_wape(sv, {k: v[1] for k, v in names.items()}).to_string())
    print("\n== 근무조 시작에 예측: 근무조 구간 순유입 평균 절대 오차 (대)")
    print(shift_mae(sv, names).to_string())

    print("\n== lead별 (07~21시 대상): 구역 시간별 대여 수 가중 오차율 / 자전거가 있던 시각의 대여소 대여 수 평균 절대 오차")
    rows = {}
    for k in LEADS:
        sk = s_val[(s_val.lead == k) & s_val.hh.between(7, 21)]
        z = sk.groupby("h")[["rent", "plain_rent", "obs_rent", "fc_rent", "now_rent"]].sum()
        u = ok(sk)
        rows[f"{k}시간 뒤"] = {**{f"구역 {n}": np.abs(z[c] - z.rent).sum() / z.rent.sum() for n, c in
                               (("베이스라인", "plain_rent"), ("예보", "fc_rent"), ("예보+오늘", "now_rent"))},
                            **{f"대여소 {n}": (u[c] - u.rent).abs().mean() for n, c in
                               (("베이스라인", "plain_rent"), ("예보", "fc_rent"), ("예보+오늘", "now_rent"))}}
    print(pd.DataFrame(rows).T.round(3).to_string())
    print("\n== 예보 + 오늘 정보 대여 모형 피처 중요도 상위 (gain 비율)\n" + (imp / imp.sum()).head(10).round(3).to_string())



if __name__ == "__main__":
    main()
