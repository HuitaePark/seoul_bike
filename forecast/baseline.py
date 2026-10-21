# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: 베이스라인 예측기. 같은 요일 유형(평일 / 주말·공휴일)·같은 시각의 최근 4주 평균 대여·반납 수를 돌려준다.

"""베이스라인 예측기 (M2, PRD 7.3 1단계). LightGBM이 이 값보다 나은지로 개선을 잰다.

예측하는 날보다 앞선 날만 쓴다(그날 자료는 쓰지 않는다). 그래서 시뮬레이터가 어느 날을 재생해도 미래를 보지 않는다.
한계: 재고가 0이던 시간에는 대여가 기록되지 않아 수요를 낮게 본다(PRD 7.3). 새 대여소는 기록이 없어 0으로 본다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from rebalancing.models import Station

from . import Forecast


class BaselinePredictor:
    def __init__(self, counts: dict[tuple[int, date, int], tuple[int, int]], holidays: set[date], until: date, weeks: int = 4):
        self.counts = counts  # (대여소, 날짜, 시) → (대여 수, 반납 수). 없는 키는 0건
        self.holidays = holidays
        self.until = until  # counts가 이 날 전날까지만 들어 있다. 이 날 이후는 예측할 수 없다
        self.weeks = weeks

    def off(self, d: date) -> bool:
        return d.weekday() >= 5 or d in self.holidays

    def predict(self, station: Station, start: datetime, horizon_h: int) -> Forecast:
        rentals, returns = [], []
        for i in range(horizon_h):
            ts = start + timedelta(hours=i)
            assert ts.date() <= self.until, f"{ts.date()}는 {self.until}까지 읽은 자료로 예측할 수 없다"
            days = [ts.date() - timedelta(days=k) for k in range(1, self.weeks * 7 + 1)]
            days = [d for d in days if self.off(d) == self.off(ts.date())]
            vals = [self.counts.get((station.id, d, ts.hour), (0, 0)) for d in days]
            rentals.append(sum(v[0] for v in vals) / len(days))
            returns.append(sum(v[1] for v in vals) / len(days))
        return Forecast(start=start, rentals_h=tuple(rentals), returns_h=tuple(returns), model_version=f"baseline-{self.weeks}w")

    @classmethod
    def from_parquet(cls, trips: str, holidays_csv: str, until: date, weeks: int = 4, since: date | None = None) -> BaselinePredictor:
        """since(기본: until − weeks주)부터 until 전날까지의 시간별 대여·반납 수를 대여이력 Parquet에서 읽는다.
        여러 날을 예측하려면 since를 첫날 − weeks주로 준다."""
        import duckdb

        since = since or until - timedelta(days=weeks * 7)
        c = duckdb.connect()
        rows = c.execute(f"""
        SELECT st, d, h, sum(r)::INT, sum(t)::INT FROM (
          SELECT rent_st AS st, rent_dt::DATE AS d, hour(rent_dt) AS h, 1 AS r, 0 AS t FROM '{trips}'
          WHERE rent_dt >= $a AND rent_dt < $b
          UNION ALL
          SELECT rtn_st, rtn_dt::DATE, hour(rtn_dt), 0, 1 FROM '{trips}'
          WHERE rtn_dt >= $a AND rtn_dt < $b AND rtn_st IS NOT NULL)
        GROUP BY ALL""", {"a": since, "b": until}).fetchall()
        holidays = {r[0] for r in c.execute(f"SELECT date::DATE FROM read_csv('{holidays_csv}')").fetchall()}
        return cls({(st, d, h): (r, t) for st, d, h, r, t in rows}, holidays, until, weeks)
