# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: 백엔드 기준 데이터(ref.stations, ref.regions) 시드 CSV를 만든다. 열 이름은 공개 API 스키마를 snake_case로 따른다.

"""ref 시드 (M2, 4 백엔드가 Flyway로 적재).

  ref_stations.csv  운영 중인 대여소만. API StationStatus의 stationId·name·lat·lon·docks·elevationM·role에 대응
  ref_regions.csv   활동 구역 17개. 거점(대여소 중심), 대여소 수, 바쁜 날 동시 트럭 하한
regionId(N01~N08, S01~S09)는 합의 전 제안이다(docs/data/agreements.md). 바뀌면 REGION_ID만 고친다.
트럭 하한은 역산 정리 7절과 같은 정의다: 시각이 1시간 안으로 좁혀지는 5대 이상 배치 방문을 시각별로 세고, 트럭 한 대가
1시간에 2번 배치한다고 본 뒤, 2025-04~10 날마다 가장 바쁜 시각의 값을 구해 그 상위 10%(p90)를 쓴다.
입력: analysis/out/stations.csv, analysis/rebal/out/visits_2025.parquet / 출력: analysis/out/ref_stations.csv, ref_regions.csv
실행: .venv/bin/python analysis/seed.py
"""
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "analysis/out"
ROLE = "CASE role WHEN '고정 회수' THEN 'FIXED_PICKUP' WHEN '고정 배치' THEN 'FIXED_DROPOFF' ELSE 'VARIABLE' END"  # 이용 기록이 없으면 VARIABLE
REGION_ID = "CASE WHEN terr LIKE '강북-%' THEN 'N' ELSE 'S' END || split_part(terr, '-', 2)"  # 강북-02 → N02, 강남-05 → S05

c = duckdb.connect()
c.execute(f"CREATE TABLE st AS SELECT * FROM '{OUT}/stations.csv' WHERE active AND terr IS NOT NULL")
c.execute(f"""COPY (
  SELECT st_no::VARCHAR AS station_id, st_id AS api_station_id, st_name AS name, {REGION_ID} AS region_id, district,
         addr AS address, lat, lon, hold_num AS docks, elev_m AS elevation_m, {ROLE} AS role
  FROM st ORDER BY st_no) TO '{OUT}/ref_stations.csv' (HEADER)""")
c.execute(f"""COPY (
  WITH v AS (SELECT t.terr, v.lo FROM '{ROOT}/analysis/rebal/out/visits_2025.parquet' v
             JOIN '{ROOT}/analysis/rebal/out/territory.csv' t ON t.st_no = v.st
             WHERE kind = 'drop' AND width_h <= 1 AND n >= 5 AND lo >= '2025-04-01' AND lo < '2025-11-01'),
  per_hour AS (SELECT terr, lo::DATE AS d, ceil(count(*) / 2.0) AS trucks FROM v GROUP BY terr, d, hour(lo)),
  per_day AS (SELECT terr, d, max(trucks) AS trucks FROM per_hour GROUP BY ALL),
  busy AS (SELECT terr, quantile_disc(trucks, 0.9)::INT AS trucks_min FROM per_day GROUP BY 1)
  SELECT {REGION_ID} AS region_id, terr AS name, CASE WHEN terr LIKE '강북-%' THEN 'NORTH' ELSE 'SOUTH' END AS side,
         count(*) AS stations, round(avg(lat), 6) AS depot_lat, round(avg(lon), 6) AS depot_lon, any_value(busy.trucks_min) AS trucks_min
  FROM st JOIN busy USING (terr) GROUP BY terr ORDER BY region_id) TO '{OUT}/ref_regions.csv' (HEADER)""")

for f in ("ref_stations.csv", "ref_regions.csv"):
    n = c.execute(f"SELECT count(*) FROM '{OUT}/{f}'").fetchone()[0]
    print(f"{f}: {n}행")
print(c.sql(f"SELECT role, count(*) AS n FROM '{OUT}/ref_stations.csv' GROUP BY 1 ORDER BY 2 DESC").df().to_string(index=False))
print(c.sql(f"SELECT region_id, name, stations, trucks_min FROM '{OUT}/ref_regions.csv'").df().to_string(index=False))
