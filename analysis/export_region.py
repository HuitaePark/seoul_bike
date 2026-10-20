# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: 활동 구역 하나(개발 기준 구역)의 대여소·대여이력·날씨·공휴일·역산 방문을 팀 공유용 묶음으로 뽑는다.

"""팀 공유 데이터 묶음 (M1).

왜: 원본(대여이력 74GB)은 공유할 수 없어서, 개발 기준 구역에 필요한 만큼만 뽑아 다른 역할이 실제 데이터로 개발하게 한다.
입력: analysis/out/stations.csv, dareungi 대여이력 Parquet(2024~2025), dareungi.duckdb wx_h(읽기 전용),
      dareungi/data/raw/calendar/holidays_2016_2026.csv, analysis/rebal/out/visits_2024·2025.parquet
출력: data/shared/<구역>/ stations.csv, trips.parquet, weather_hourly.parquet, holidays.csv, rebal_visits.parquet, README.md
실행: /Users/junyeop/dareungi/.venv/bin/python analysis/export_region.py 강남-05
"""
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DAR = Path("/Users/junyeop/dareungi")
TERR = sys.argv[1]
OUT = ROOT / "data/shared" / TERR
A, B = "2024-01-01", "2026-01-01"

OUT.mkdir(parents=True, exist_ok=True)
c = duckdb.connect()
c.execute(f"CREATE TABLE st AS SELECT * FROM '{ROOT}/analysis/out/stations.csv' WHERE terr = '{TERR}'")
assert c.execute("SELECT count(*) FROM st").fetchone()[0] > 0, f"구역 {TERR}에 대여소가 없다"
c.execute(f"ATTACH '{DAR}/data/db/dareungi.duckdb' AS dar (READ_ONLY)")
rent = (DAR / "data/processed/rent/y=202[45]/*/*.parquet").as_posix()

c.execute(f"COPY st TO '{OUT}/stations.csv' (HEADER)")
# 권역 대여소가 출발이나 도착인 대여. 완전 중복(같은 자전거·대여일시·대여소)은 dareungi trips와 같은 규칙으로 지운다
c.execute(f"""COPY (
  SELECT DISTINCT ON (bike_id, rent_dt, rent_st) bike_id, rent_dt, rent_st, rtn_dt, rtn_st, use_min, use_m
  FROM read_parquet('{rent}')
  WHERE rent_dt >= '{A}' AND rent_dt < '{B}'
    AND (rent_st IN (SELECT st_no FROM st) OR rtn_st IN (SELECT st_no FROM st))
  ORDER BY rent_dt) TO '{OUT}/trips.parquet' (FORMAT parquet, COMPRESSION zstd)""")
c.execute(f"COPY (SELECT * FROM dar.wx_h WHERE ts >= '{A}' AND ts < '{B}' ORDER BY ts) TO '{OUT}/weather_hourly.parquet' (FORMAT parquet)")
c.execute(f"""COPY (SELECT * FROM read_csv('{DAR}/data/raw/calendar/holidays_2016_2026.csv')
              WHERE date >= '{A}' AND date < '{B}') TO '{OUT}/holidays.csv' (HEADER)""")
c.execute(f"""COPY (SELECT st, kind, n, lo, hi, width_h, n_partner
              FROM read_parquet(['{ROOT}/analysis/rebal/out/visits_2024.parquet', '{ROOT}/analysis/rebal/out/visits_2025.parquet'])
              WHERE st IN (SELECT st_no FROM st) AND lo < '{B}' ORDER BY lo) TO '{OUT}/rebal_visits.parquet' (FORMAT parquet)""")

counts = {f.name: c.execute(f"SELECT count(*) FROM '{f}'").fetchone()[0] for f in sorted(OUT.glob("*.*")) if f.suffix in (".csv", ".parquet")}
inside = c.execute(f"""SELECT round(100 * avg((rent_st IN (SELECT st_no FROM st) AND rtn_st IN (SELECT st_no FROM st))::INT), 1)
                       FROM '{OUT}/trips.parquet'""").fetchone()[0]
for name, n in counts.items():
    print(f"{name:24} {n:>12,}행  {(OUT / name).stat().st_size / 1e6:8.1f}MB")

(OUT / "README.md").write_text(f"""# 공유 데이터: {TERR}

`analysis/export_region.py {TERR}`로 만든 묶음. 기간 {A} ~ 2025-12-31, 시각은 모두 KST. 열 설명은 `docs/data/data-spec.md`.

| 파일 | 행 | 내용 |
| --- | --- | --- |
| stations.csv | {counts['stations.csv']:,} | 권역 대여소 (번호 `st_no` ↔ 실시간 `st_id`, 위치, 거치대, 고도, 역할) |
| trips.parquet | {counts['trips.parquet']:,} | 권역 대여소가 출발이나 도착인 대여. 양 끝이 모두 권역 안인 것은 {inside}% |
| weather_hourly.parquet | {counts['weather_hourly.parquet']:,} | 서울(108) ASOS 시간자료 |
| holidays.csv | {counts['holidays.csv']:,} | 공휴일 |
| rebal_visits.parquet | {counts['rebal_visits.parquet']:,} | 역산한 트럭 방문 (`kind`: pick 회수, drop 배치). 추정치 |

출처: 서울 열린데이터광장(공공자전거 대여이력·대여소 정보), 기상청 ASOS, 공휴일은 공공데이터포털 특일정보.
""")
print(f"저장: {OUT.relative_to(ROOT)}")
