# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: LightGBM 예측기. 대여소·시각별 대여·반납 수를 예측하고, 베이스라인과 같은 채점으로 비교한다.

"""LightGBM 예측기 (PRD 7.3 2단계).

모형: 대여 수, 반납 수를 각각 포아송 손실로 예측한다.
재고 0 보정: 대여는 자전거가 있어야 기록되므로, 재고가 0이던 시간이 EMPTY_MAX분 이상인 시각은 대여 모형 학습에서 뺀다.
           반납은 거치대에 막히지 않으므로 모두 쓴다. 4주 대여율 피처도 자전거가 있던 시간으로 나눈다(sim/latent.py와 같은 방식).
미래 정보 차단: 모든 피처는 그 시각 이전 자료로 만든다(4주 평균·지연값은 전날까지). 대여소 역할·하루 평균(stations.csv)은
              2025-04~10 자료로 만든 값이라 쓰지 않는다. 날씨는 관측값이다(운영할 때는 예보라 차이를 따로 봐야 한다).
기간: 학습 2024-02 ~ 2025-02, 조기 종료 2025-03, 검증 2025-04 ~ 06. 평가 기간(2025-07 ~ 10)은 쓰지 않는다.
실행: .venv/bin/python -m forecast.lgbm --terr 강남-05
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta

import duckdb
import lightgbm as lgb
import pandas as pd

from rebalancing.models import Station
from sim.engine import ROOT, SHIFTS
from sim.latent import empty_segments, hourly_empty

from . import Forecast

EMPTY_MAX = 6  # 한 시각 안에 재고 0이 이 분 이상이면 대여 모형 학습에서 뺀다 (10%)
FEATS = ["hh", "dow", "off", "mon", "base_rent", "plain_rent", "base_ret", "lag_rent_1d", "lag_rent_1w",
         "lag_ret_1d", "lag_ret_1w", "temp", "rain_mm", "rain_prev3h", "rel_elev", "docks"]


def features(terr: str, a: date, b: date) -> pd.DataFrame:
    """[a, b) 동안 구역 대여소 × 시각마다 한 행. 대여·반납 수, 재고 0 분, 피처."""
    shared = ROOT / "data/shared" / terr
    pad = datetime.combine(a - timedelta(days=35), datetime.min.time())  # 4주 평균과 1주 전 값을 위해 앞을 더 읽는다
    t1 = datetime.combine(b, datetime.min.time())
    c = duckdb.connect()
    c.execute(f"CREATE VIEW tr AS SELECT rent_dt, rent_st, rtn_dt, rtn_st FROM '{shared}/trips.parquet' WHERE rtn_st IS NOT NULL")
    c.execute(f"""CREATE TABLE zs AS SELECT st_no AS st, hold_num AS docks, rel_elev FROM '{ROOT}/analysis/out/stations.csv'
                  WHERE terr = ? AND st_no IN (SELECT rent_st FROM tr WHERE rent_dt >= $p AND rent_dt < $b)""".replace("?", f"'{terr}'"),
              {"p": pad, "b": t1})
    stations = {r[0] for r in c.execute("SELECT st FROM zs").fetchall()}
    empty_segments(c, ROOT / "analysis/rebal/out", pad, t1, stations)
    hourly_empty(c, t1)
    holidays = ",".join(repr(str(r[0])) for r in c.execute(f"SELECT date::DATE FROM read_csv('{shared}/holidays.csv')").fetchall())
    return c.execute(f"""
    WITH grid AS (SELECT st, h FROM zs, generate_series($p, $b - INTERVAL 1 HOUR, INTERVAL 1 HOUR) t(h)),
    rn AS (SELECT rent_st AS st, date_trunc('hour', rent_dt) AS h, count(*) AS n FROM tr WHERE rent_dt >= $p AND rent_dt < $b GROUP BY ALL),
    rt AS (SELECT rtn_st AS st, date_trunc('hour', rtn_dt) AS h, count(*) AS n FROM tr WHERE rtn_dt >= $p AND rtn_dt < $b GROUP BY ALL),
    base AS (
      SELECT g.st, g.h, coalesce(rn.n, 0) AS rent, coalesce(rt.n, 0) AS ret, coalesce(zh.empty_min, 0) AS empty_min,
             (60 - least(coalesce(zh.empty_min, 0), 60)) / 60 AS avail, hour(g.h) AS hh, isodow(g.h) AS dow, month(g.h) AS mon,
             (isodow(g.h) >= 6 OR g.h::DATE IN ({holidays}))::INT AS off
      FROM grid g LEFT JOIN rn USING (st, h) LEFT JOIN rt USING (st, h) LEFT JOIN zh USING (st, h)),
    f AS (
      SELECT *,
        sum(rent) OVER w28 / NULLIF(sum(avail) OVER w28, 0) AS base_rent,  -- 자전거가 있던 시간 기준 대여율
        avg(rent) OVER w28 AS plain_rent,                                  -- 베이스라인(forecast/baseline.py)과 같은 값
        avg(ret) OVER w28 AS base_ret,
        lag(rent, 1) OVER wd AS lag_rent_1d, lag(rent, 7) OVER wd AS lag_rent_1w,
        lag(ret, 1) OVER wd AS lag_ret_1d, lag(ret, 7) OVER wd AS lag_ret_1w
      FROM base
      WINDOW w28 AS (PARTITION BY st, hh, off ORDER BY h RANGE BETWEEN INTERVAL 28 DAYS PRECEDING AND INTERVAL 1 DAY PRECEDING),
             wd AS (PARTITION BY st, hh ORDER BY h))
    SELECT f.*, w.temp, w.rain_mm, w.rain_prev3h, zs.rel_elev, zs.docks
    FROM f JOIN zs USING (st) LEFT JOIN '{shared}/weather_hourly.parquet' w ON w.ts = f.h
    WHERE f.h >= $a ORDER BY st, h""", {"p": pad, "a": datetime.combine(a, datetime.min.time()), "b": t1}).df()


PARAMS = dict(objective="poisson", learning_rate=0.05, num_leaves=63, min_data_in_leaf=100, bagging_fraction=0.8, bagging_freq=1,
              feature_fraction=0.8, seed=0, deterministic=True, verbose=-1)


def fit(train: pd.DataFrame, stop: pd.DataFrame, target: str, feats: list[str] = FEATS) -> lgb.Booster:
    """stop(학습 다음 한 달)으로 조기 종료한다. 검증 기간은 고르는 데 쓰지 않는다."""
    return lgb.train(PARAMS, lgb.Dataset(train[feats], train[target]), num_boost_round=2000,
                     valid_sets=[lgb.Dataset(stop[feats], stop[target])], callbacks=[lgb.early_stopping(50, verbose=False)])


class LightGBMPredictor:
    """DemandPredictor 구현. 미리 만든 피처 표의 시각만 예측할 수 있다."""

    def __init__(self, rent: lgb.Booster, ret: lgb.Booster, frame: pd.DataFrame, version: str):
        self.version = version
        self.pred = dict(zip(zip(frame.st, frame.h), zip(rent.predict(frame[FEATS]), ret.predict(frame[FEATS]))))

    def predict(self, station: Station, start: datetime, horizon_h: int) -> Forecast:
        vals = [self.pred[(station.id, start + timedelta(hours=i))] for i in range(horizon_h)]
        return Forecast(start=start, rentals_h=tuple(v[0] for v in vals), returns_h=tuple(v[1] for v in vals), model_version=self.version)


def shift_mae(df: pd.DataFrame, cols: dict[str, tuple[str, str]]) -> pd.DataFrame:
    """근무조 구간(SHIFTS)마다 대여소별 순유입(반납 − 대여)을 더해 평균 절대 오차를 낸다. cols: 이름 → (예측 반납 열, 예측 대여 열)."""
    shift = {h: i for i, (h0, h1) in enumerate(SHIFTS) for h in range(h0, h1)}
    d = df[df.hh.isin(shift)].assign(shift=lambda x: x.hh.map(shift), day=lambda x: x.h.dt.date, month=lambda x: x.h.dt.strftime("%Y-%m"))
    d = d.assign(actual=d.ret - d.rent, **{k: d[r] - d[n] for k, (r, n) in cols.items()})
    g = d.groupby(["month", "st", "day", "shift"])[["actual", *cols]].sum()
    return pd.DataFrame({k: (g[k] - g.actual).abs().groupby("month").mean() for k in cols}).round(2)


def shift_start_stock(df: pd.DataFrame) -> pd.Series:
    """근무조 시작 시각마다 대여소 재고(역산 재고 복원, 초기 재고와 같은 방식). index (st, h)."""
    starts = sorted(df.h[df.hh.isin([h0 for h0, _ in SHIFTS])].drop_duplicates().dt.to_pydatetime())
    c = duckdb.connect()
    empty_segments(c, ROOT / "analysis/rebal/out", starts[0], starts[-1] + timedelta(hours=1), set(df.st.unique()))
    c.execute("CREATE TABLE o AS SELECT unnest($o) AS t", {"o": starts})
    return c.execute("SELECT iv.st, o.t AS h, count(*) AS s0 FROM o JOIN iv ON iv.s <= o.t AND iv.e > o.t GROUP BY ALL").df().set_index(["st", "h"]).s0


def stockout(df: pd.DataFrame, s0: pd.Series, cols: dict[str, tuple[str, str]], top: int = 20) -> pd.DataFrame:
    """근무조마다 시작 재고에서 출발한 재고 궤적의 최저값으로 '이번 근무조에 빌 대여소'를 맞히는지 본다.
    실제로 비는지는 트럭 이동 없이 기록된 대여·반납만으로 판단한다(수요만으로 비었을지). 이미 빈 대여소는 뺀다."""
    shift = {h: i for i, (h0, h1) in enumerate(SHIFTS) for h in range(h0, h1)}
    d = df[df.hh.isin(shift)].sort_values(["st", "h"])
    d = d.assign(day=d.h.dt.normalize(), sh=d.hh.map(shift))
    start = d.day + pd.to_timedelta(d.sh.map(lambda i: SHIFTS[i][0]), unit="h")
    d["s0"] = s0.reindex(pd.MultiIndex.from_arrays([d.st, start])).fillna(0).to_numpy()
    keys = ["st", "day", "sh"]
    low = lambda r, n: (d.s0 + (d[r] - d[n]).groupby([d[k] for k in keys]).cumsum()).groupby([d[k] for k in keys]).min()  # noqa: E731
    m = pd.DataFrame({"actual": low("ret", "rent"), **{k: low(r, n) for k, (r, n) in cols.items()}})
    m = m[d.groupby(keys).s0.first() > 0]
    y = m.actual <= 0
    out = {}
    for k in cols:
        score, pred = -m[k], m[k] <= 0.5
        rank = score.rank()
        tp = (pred & y).sum()
        topk = m.assign(score=score, y=y).sort_values("score", ascending=False).groupby(level=["day", "sh"]).head(top).y.mean()
        out[k] = {"순위 AUC": (rank[y].sum() - y.sum() * (y.sum() + 1) / 2) / (y.sum() * (~y).sum()),
                  "빈 곳 중 잡은 비율": tp / y.sum(), "'빈다'고 한 곳의 적중": tp / max(pred.sum(), 1), f"근무조마다 위험 상위 {top}곳 적중": topk}
    print(f"   (대여소·근무조 {len(m):,}건 중 실제로 빈 곳 {y.mean():.1%})")
    return pd.DataFrame(out).T.round(3)


def zone_wape(df: pd.DataFrame, cols: dict[str, str]) -> pd.DataFrame:
    """구역 대여소를 합친 시간별 대여 수의 가중 오차율(오차 합 ÷ 실제 합), 07~21시. 비 온 시각(강수 > 0)을 따로 낸다."""
    z = df[df.hh.between(7, 21)].groupby("h").agg(rent=("rent", "sum"), rain=("rain_mm", "max"), **{k: (c, "sum") for k, c in cols.items()})
    parts = {"전체": z, "비 안 온 시각": z[z.rain == 0], "비 온 시각": z[z.rain > 0]}
    return pd.DataFrame({p: {k: (s[k] - s.rent).abs().sum() / s.rent.sum() for k in cols} for p, s in parts.items()}).round(3)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--terr", default="강남-05")
    args = ap.parse_args()

    df = features(args.terr, date(2024, 2, 1), date(2025, 7, 1))
    train, stop, valid = df[df.h < "2025-03-01"], df[(df.h >= "2025-03-01") & (df.h < "2025-04-01")], df[df.h >= "2025-04-01"].copy()
    ok = lambda x: x[x.empty_min < EMPTY_MAX]  # noqa: E731  재고 0 보정: 대여 모형은 자전거가 있던 시각만
    m_rent, m_ret = fit(ok(train), ok(stop), "rent"), fit(train, stop, "ret")
    valid["lgb_rent"], valid["lgb_ret"] = m_rent.predict(valid[FEATS]), m_ret.predict(valid[FEATS])
    print(f"{args.terr}  학습 {len(train):,}행(대여 모형은 재고 0 보정 뒤 {len(ok(train)):,}행)  검증 {len(valid):,}행  "
          f"나무 수 대여 {m_rent.best_iteration} · 반납 {m_ret.best_iteration}")

    # 베이스라인 4주는 forecast/baseline.py와 같은 정의다. 대여소 범위(2024년부터 대여가 있던 곳 전체)와 반납 대여소가 빈 대여 제외가 달라
    # python -m forecast의 값과는 조금 다르다. 같은 표 안의 열은 같은 행으로 채점한다
    print("\n== 근무조 구간 순유입 평균 절대 오차 (대)")
    print(shift_mae(valid, {"베이스라인 4주": ("base_ret", "plain_rent"), "재고 0 보정 4주": ("base_ret", "base_rent"),
                            "LightGBM": ("lgb_ret", "lgb_rent")}).to_string())
    u = ok(valid)
    print(f"\n== 자전거가 있던 시각의 대여 수 평균 절대 오차 (대여소·시각, {len(u):,}행)")
    print(pd.Series({"베이스라인 4주": (u.plain_rent - u.rent).abs().mean(), "재고 0 보정 4주": (u.base_rent - u.rent).abs().mean(),
                     "LightGBM": (u.lgb_rent - u.rent).abs().mean()}).round(3).to_string())
    # 재배치에 맞춘 지표: 대여소 하나의 순유입 오차는 우연이 대부분이라 차이가 작게 보인다
    print("\n== 근무조에 빌 대여소 맞히기 (트럭을 보낼 곳을 고르는 정확도)")
    print(stockout(valid, shift_start_stock(valid), {"베이스라인 4주": ("base_ret", "plain_rent"), "LightGBM": ("lgb_ret", "lgb_rent")}).to_string())
    print("\n== 구역 전체 시간별 대여 수 가중 오차율 (필요한 트럭 규모)")
    print(zone_wape(valid, {"베이스라인 4주": "plain_rent", "LightGBM": "lgb_rent"}).to_string())
    imp = pd.Series(m_rent.feature_importance("gain"), index=FEATS).sort_values(ascending=False)
    print("\n== 대여 모형 피처 중요도 상위 (gain 비율)\n" + (imp / imp.sum()).head(8).round(3).to_string())

    out = ROOT / "data/models"
    out.mkdir(parents=True, exist_ok=True)
    for name, m in (("rent", m_rent), ("ret", m_ret)):
        m.save_model(str(out / f"lgbm_{args.terr}_{name}.txt"))
    print(f"\n저장: data/models/lgbm_{args.terr}_rent.txt, _ret.txt")


if __name__ == "__main__":
    main()
