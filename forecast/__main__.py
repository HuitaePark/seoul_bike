# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: 예측기를 한 달 동안의 근무조 구간에 대해 채점한다. 대여소별 3시간 순유입(반납 − 대여)의 평균 절대 오차.

"""실행: .venv/bin/python -m forecast --terr 강남-05 --month 2025-06

비교 대상: 0(변화 없음), 1주 전 같은 시각, 베이스라인(같은 요일 유형·시각의 최근 4주 평균).
평가 기간(2025-07 ~ 10)은 모형 선택에 쓰지 않으므로 검증 기간(2025-04 ~ 06)의 달로 돌린다.
"""
import argparse
from datetime import date, datetime, timedelta

from rebalancing.models import Station

from sim.engine import ROOT, SHIFTS  # 근무조 정의를 시뮬레이터와 같이 쓴다

from .baseline import BaselinePredictor


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--terr", default="강남-05")
    ap.add_argument("--month", default="2025-06")
    args = ap.parse_args()

    first = date.fromisoformat(args.month + "-01")
    last = (first + timedelta(days=32)).replace(day=1)
    shared = ROOT / "data/shared" / args.terr
    p = BaselinePredictor.from_parquet(str(shared / "trips.parquet"), str(shared / "holidays.csv"), until=last,
                                       since=first - timedelta(weeks=4))  # 첫날의 4주 창까지 읽는다
    import duckdb  # 대여이력 묶음에는 구역 밖 상대 대여소도 있어서, 채점은 구역 안 대여소만 한다
    zone = {r[0] for r in duckdb.sql(f"SELECT st_no FROM '{ROOT}/analysis/out/stations.csv' WHERE terr = '{args.terr}'").fetchall()}
    stations = sorted({st for st, _, _ in p.counts} & zone)

    err = {"0 (변화 없음)": 0.0, "1주 전 같은 시각": 0.0, "베이스라인 4주": 0.0}
    n = 0
    d = first
    while d < last:
        for h0, h1 in SHIFTS:
            start = datetime.combine(d, datetime.min.time()) + timedelta(hours=h0)
            hours = [(start + timedelta(hours=i)) for i in range(h1 - h0)]
            for st in stations:
                net = lambda day: sum(p.counts.get((st, day, t.hour), (0, 0))[1] - p.counts.get((st, day, t.hour), (0, 0))[0]  # noqa: E731
                                      for t in hours)
                actual = net(d)
                f = p.predict(Station(st, "", 0, 0, 0, 0), start, h1 - h0)
                err["0 (변화 없음)"] += abs(actual)
                err["1주 전 같은 시각"] += abs(actual - net(d - timedelta(days=7)))
                err["베이스라인 4주"] += abs(actual - (f.returns - f.rentals))
                n += 1
        d += timedelta(days=1)
    print(f"{args.terr} {args.month}  대여소 {len(stations)}곳 × 근무조 구간 {n // len(stations)}개")
    for name, e in err.items():
        print(f"  {name:14} 근무조 구간 순유입 평균 절대 오차 {e / n:.2f}대")


if __name__ == "__main__":
    main()
