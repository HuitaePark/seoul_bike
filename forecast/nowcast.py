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
from datetime import date, datetime, time, timedelta

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd

from rebalancing.models import Station

from . import Forecast
from .lgbm import EMPTY_MAX, FEATS, ROOT, SHIFTS, features, fit, shift_mae, shift_start_stock, stockout, zone_wape

LEADS = range(1, 6)
SAMPLE = 0.3
OBSERVED = ["temp", "rain_mm", "rain_prev3h"]
FEATS_F = [f for f in FEATS if f not in OBSERVED] + ["lead", "f_temp", "f_pcp", "f_pop"]
FEATS_N = FEATS_F + ["rent_rec1", "rent_rec3", "ret_rec1", "ret_rec3", "exp_rent3", "exp_ret3", "zone_ratio3"]
EV_FEATS = ["ev_arrive", "ev_exit", "ev_type", "ev_closed"]  # --events: 행사장 700m 안 대여소의 행사 시간대 (analysis/events.py)


def with_events(df: pd.DataFrame, ev: pd.DataFrame | None = None) -> pd.DataFrame:
    """행사 달력(analysis/out/events.csv)으로 대여소·시각마다 행사 피처를 붙인다. 행사는 미리 알려진 일정이라 미래 정보가 아니다.
    ev: 행사 × 근처 대여소 표(st_no 열 포함). 주지 않으면 analysis/out의 두 표를 합친다.
    한 대여소에 같은 날 행사가 둘이면(잠실야구장·실내체육관처럼 가까운 행사장) 시각마다 하나라도 해당하면 1로 본다."""
    if ev is None:
        ev = pd.read_csv(ROOT / "analysis/out/events.csv").merge(pd.read_csv(ROOT / "analysis/out/event_stations.csv"), on="venue")
    ev = ev.assign(date=pd.to_datetime(ev.date)).rename(columns={"st_no": "st"})
    m = df[["st", "h", "hh"]].assign(date=df.h.dt.normalize(), i=np.arange(len(df))).merge(ev, on=["st", "date"], how="left")
    f = pd.DataFrame({"i": m.i, "ev_arrive": (m.hh >= m.arrive_h0) & (m.hh < m.arrive_h1), "ev_exit": (m.hh >= m.exit_h0) & (m.hh < m.exit_h1),
                      "ev_type": m.type.fillna(0),  # analysis/events.py의 KINDS 순번(축구 1, 야구 2, 콘서트 3, ...)
                      "ev_closed": m.closed.fillna(0)}).groupby("i").max()
    out = df.copy()
    out[EV_FEATS] = f[EV_FEATS].astype("int8").to_numpy()
    return out


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
        p = d[["st", "h", "hh", "rent", "ret", "empty_min", "rain_mm", "plain_rent", "base_ret", *[f for f in FEATS if f not in OBSERVED],
               *[c for c in EV_FEATS if c in d]]].copy()
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
        feats = rent.feature_name()  # 행사 피처를 쓴 모형이면 그 피처까지
        self.pred = dict(zip(zip(frame.st, frame.h, frame.lead), zip(rent.predict(frame[feats]), ret.predict(frame[feats]))))

    def predict(self, station: Station, start: datetime, horizon_h: int) -> Forecast:
        assert horizon_h <= max(LEADS), f"최대 {max(LEADS)}시간까지 예측한다"
        # 피처 표에 없는 대여소(예측 기간에 대여 기록이 없던 신설 등)는 수요 0으로 본다
        vals = [self.pred.get((station.id, start + timedelta(hours=i), i + 1), (0.0, 0.0)) for i in range(horizon_h)]
        return Forecast(start=start, rentals_h=tuple(v[0] for v in vals), returns_h=tuple(v[1] for v in vals), model_version=self.version)


def load_predictor(terr: str, d: date, model_terr: str | None = None) -> NowcastPredictor:
    """d일 하루를 예측할 수 있게 terr의 피처를 만들고, 저장된 모형(data/models/nowcast_<model_terr>_rent·ret.txt)을 불러온다.
    model_terr를 주면 다른 구역에서 학습한 모형을 옮겨 쓴다. 모형은 2025-02까지로 학습했으므로 그 뒤의 날만 미래를 보지 않는다."""
    df = with_events(with_lags(features(terr, d - timedelta(days=1), d + timedelta(days=1))))  # 행사 피처는 그 피처로 학습한 모형만 쓴다
    df = df[df.h >= datetime.combine(d, time())]
    mt = model_terr or terr
    m = {n: lgb.Booster(model_file=str(ROOT / f"data/models/nowcast_{mt}_{n}.txt")) for n in ("rent", "ret")}
    return NowcastPredictor(m["rent"], m["ret"], stack(df, forecast_weather(df.h), 1.0), f"nowcast-{mt}")


def at_shift_start(s: pd.DataFrame) -> pd.DataFrame:
    """근무조 시작에 예측했다고 보고, 근무조 각 시각을 그 시각까지의 lead(1, 2, 3)로 고른다."""
    lead = {h: h - h0 + 1 for h0, h1 in SHIFTS for h in range(h0, h1)}
    return s[s.hh.map(lead) == s.lead]


def transfer(terr: str, model_terr: str) -> None:
    """학습하지 않고 model_terr에서 학습해 저장한 모형으로 terr의 검증 기간(2025-04~06)을 채점한다(옮겨 쓰기 확인)."""
    df = with_lags(features(terr, date(2025, 3, 31), date(2025, 7, 1)))
    valid = df[df.h >= "2025-04-01"]
    s_val = stack(valid, forecast_weather(valid.h), 1.0)
    for n in ("rent", "ret"):
        s_val[f"now_{n}"] = lgb.Booster(model_file=str(ROOT / f"data/models/nowcast_{model_terr}_{n}.txt")).predict(s_val[FEATS_N])
    names = {"베이스라인 4주": ("base_ret", "plain_rent"), f"{model_terr} 모형": ("now_ret", "now_rent")}
    sv = at_shift_start(s_val)
    print(f"{terr}를 {model_terr} 모형으로 채점 (검증 2025-04~06)")
    print("\n== 근무조에 빌 대여소 맞히기\n" + stockout(sv, shift_start_stock(sv), names).to_string())
    print("\n== 구역 전체 시간별 대여 수 가중 오차율\n" + zone_wape(sv, {k: v[1] for k, v in names.items()}).to_string())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--terr", default="강남-05")
    ap.add_argument("--model", help="주면 학습하지 않고 이 구역의 저장된 모형으로 --terr를 채점한다(옮겨 쓰기 확인)")
    ap.add_argument("--events", action="store_true", help="행사 피처를 넣은 모형을 학습해 같은 구역의 기존 모형과 비교한다")
    args = ap.parse_args()
    if args.model:
        return transfer(args.terr, args.model)
    if args.events:
        return events_compare(args.terr)

    df = with_lags(features(args.terr, date(2024, 1, 31), date(2025, 7, 1)))
    df = df[df.h >= "2024-02-01"]
    fw = forecast_weather(df.h)
    tr = df[df.h < "2025-03-01"]
    stop, valid = df[(df.h >= "2025-03-01") & (df.h < "2025-04-01")], df[df.h >= "2025-04-01"]
    s_tr, s_stop, s_val = stack(tr, fw, SAMPLE), stack(stop, fw, SAMPLE), stack(valid, fw, 1.0)
    ok = lambda x: x[x.empty_min < EMPTY_MAX]  # noqa: E731
    print(f"{args.terr}  학습 {len(s_tr):,}행(lead 5개 × {SAMPLE:.0%})  검증 {len(s_val):,}행  예보 없는 행 {s_val.f_temp.isna().mean():.1%}")

    names = {"베이스라인 4주": ("base_ret", "plain_rent")}
    if (ROOT / f"data/models/lgbm_{args.terr}_rent.txt").exists():  # 관측 날씨 모형(lgbm.py)은 참고용이라 있을 때만 비교한다
        old = {n: lgb.Booster(model_file=str(ROOT / f"data/models/lgbm_{args.terr}_{n}.txt")) for n in ("rent", "ret")}
        obs = valid.set_index(["st", "h"])[FEATS]
        s_val = s_val.join(pd.DataFrame({"obs_rent": old["rent"].predict(obs), "obs_ret": old["ret"].predict(obs)}, index=obs.index), on=["st", "h"])
        names["관측 날씨(참고)"] = ("obs_ret", "obs_rent")
    for name, feats in (("fc", FEATS_F), ("now", FEATS_N)):
        m_rent, m_ret = fit(ok(s_tr), ok(s_stop), "rent", feats), fit(s_tr, s_stop, "ret", feats)
        s_val[f"{name}_rent"], s_val[f"{name}_ret"] = m_rent.predict(s_val[feats]), m_ret.predict(s_val[feats])
        print(f"  {name}: 나무 수 대여 {m_rent.best_iteration} · 반납 {m_ret.best_iteration}")
        if name == "now":
            out = ROOT / "data/models"
            m_rent.save_model(str(out / f"nowcast_{args.terr}_rent.txt"))
            m_ret.save_model(str(out / f"nowcast_{args.terr}_ret.txt"))
            imp = pd.Series(m_rent.feature_importance("gain"), index=feats).sort_values(ascending=False)

    names |= {"예보 날씨": ("fc_ret", "fc_rent"), "예보 + 오늘 정보": ("now_ret", "now_rent")}
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
        z = sk.groupby("h")[["rent", "plain_rent", "fc_rent", "now_rent"]].sum()
        u = ok(sk)
        rows[f"{k}시간 뒤"] = {**{f"구역 {n}": np.abs(z[c] - z.rent).sum() / z.rent.sum() for n, c in
                               (("베이스라인", "plain_rent"), ("예보", "fc_rent"), ("예보+오늘", "now_rent"))},
                            **{f"대여소 {n}": (u[c] - u.rent).abs().mean() for n, c in
                               (("베이스라인", "plain_rent"), ("예보", "fc_rent"), ("예보+오늘", "now_rent"))}}
    print(pd.DataFrame(rows).T.round(3).to_string())
    print("\n== 예보 + 오늘 정보 대여 모형 피처 중요도 상위 (gain 비율)\n" + (imp / imp.sum()).head(10).round(3).to_string())


def events_compare(terr: str) -> None:
    """행사 피처를 넣은 모형(nowcast_<구역>_ev)을 학습하고, 같은 구역의 행사 피처 없는 모형(nowcast_<구역>)과 비교한다.
    전체 지표와 함께 행사 시간대(행사장 700m 안 대여소의 도착·퇴장 시각)만 떼어 대여·반납 오차를 본다."""
    df = with_events(with_lags(features(terr, date(2024, 1, 31), date(2025, 7, 1))))
    df = df[df.h >= "2024-02-01"]
    fw = forecast_weather(df.h)
    tr, stop, valid = df[df.h < "2025-03-01"], df[(df.h >= "2025-03-01") & (df.h < "2025-04-01")], df[df.h >= "2025-04-01"]
    s_tr, s_stop, s_val = stack(tr, fw, SAMPLE), stack(stop, fw, SAMPLE), stack(valid, fw, 1.0)
    ok = lambda x: x[x.empty_min < EMPTY_MAX]  # noqa: E731
    feats = FEATS_N + EV_FEATS
    print(f"{terr}  학습 행 중 행사 시간대 {(s_tr.ev_arrive | s_tr.ev_exit).sum():,}행, 검증 {(s_val.ev_arrive | s_val.ev_exit).sum():,}행")
    m_rent, m_ret = fit(ok(s_tr), ok(s_stop), "rent", feats), fit(s_tr, s_stop, "ret", feats)
    m_rent.save_model(str(ROOT / f"data/models/nowcast_{terr}_ev_rent.txt"))
    m_ret.save_model(str(ROOT / f"data/models/nowcast_{terr}_ev_ret.txt"))
    s_val["ev_rent"], s_val["ev_ret"] = m_rent.predict(s_val[feats]), m_ret.predict(s_val[feats])
    for n in ("rent", "ret"):
        s_val[f"now_{n}"] = lgb.Booster(model_file=str(ROOT / f"data/models/nowcast_{terr}_{n}.txt")).predict(s_val[FEATS_N])
    names = {"베이스라인 4주": ("base_ret", "plain_rent"), "행사 피처 없음": ("now_ret", "now_rent"), "행사 피처 있음": ("ev_ret", "ev_rent")}
    sv = at_shift_start(s_val)
    print("\n== 근무조에 빌 대여소 맞히기\n" + stockout(sv, shift_start_stock(sv), names).to_string())
    print("\n== 구역 전체 시간별 대여 수 가중 오차율\n" + zone_wape(sv, {k: v[1] for k, v in names.items()}).to_string())
    e = s_val[(s_val.ev_arrive == 1) | (s_val.ev_exit == 1)]
    print(f"\n== 행사 시간대만 (검증 {e.h.dt.date.nunique()}일, 대여소·시각·lead {len(e):,}행): 평균 절대 오차")
    rows = {}
    for (d, phase), g in e.assign(phase=np.where(e.ev_exit == 1, "퇴장", "도착")).groupby([e.h.dt.date, "phase"]):
        rows[f"{d} {phase}"] = {f"{k} {what}": (g[c] - g[what]).abs().mean() for k, (r, n) in names.items()
                                for what, c in (("rent", n), ("ret", r))}
    print(pd.DataFrame(rows).T.round(2).to_string())



if __name__ == "__main__":
    main()
