"""재배치 역산 1단계: 자전거별 '반납 → 다음 대여' 구간(gap)을 만든다. (팀 프로젝트 탐색용, 본 프로젝트와 무관한 임시 파일)

무엇을: 같은 자전거의 연속한 두 대여에서 직전 반납 대여소(from_st)와 다음 대여 대여소(to_st)를 잇는다.
        from_st ≠ to_st 이면 사람이 타지 않고 옮겨진 것(재배치·정비 수거·기록 오류)이다. 옮긴 시각은 [t_from, t_to] 사이 어딘가.
왜: 공개 데이터에는 트럭 재배치 기록이 없어서, 대여이력의 '순간이동'으로 역산하는 것이 유일한 방법이다.
입력: 본 프로젝트의 data/processed/rent(읽기만), fail_std.parquet(고장신고), data/interim/station_master.csv
출력: out/gaps_<기간>.parquet (구간 한 개 = 한 행)
실행: python 01_gaps.py 2025-05   (한 달 시험)  /  python 01_gaps.py 2025   (한 해)
"""
import sys
import os
from pathlib import Path

import duckdb

DAR = os.environ.get("DAREUNGI", "/Users/junyeop/dareungi")  # 따릉이 고장신고 프로젝트(대여이력 원본) 위치. README 참고
ROOT = DAR
HERE = Path(__file__).parent
(HERE / "out").mkdir(exist_ok=True)
(HERE / "tmp").mkdir(exist_ok=True)

period = sys.argv[1]                                   # '2025' 또는 '2025-05'
start = f"{period}-01-01" if len(period) == 4 else f"{period}-01"
end = f"{int(period) + 1}-01-01" if len(period) == 4 else f"{start[:5]}{int(period[5:]) + 1:02d}-01"
if end.endswith("-13-01"):
    end = f"{int(period[:4]) + 1}-01-01"

c = duckdb.connect()                                   # 메모리 DB: 본 프로젝트 DuckDB 파일을 잠그지 않는다
c.execute(f"SET memory_limit='8GB'; SET threads=6; SET temp_directory='{HERE / 'tmp'}'")

# 기간 시작 한 달 전부터 읽어야 첫 대여의 직전 반납을 안다
c.execute(f"""
CREATE TABLE t AS
SELECT bike_id, rent_dt, rent_st, rtn_dt, rtn_st
FROM read_parquet('{ROOT}/data/processed/rent/*/*/*.parquet', hive_partitioning=true)
WHERE rent_dt >= TIMESTAMP '{start}' - INTERVAL 31 DAY AND rent_dt < TIMESTAMP '{end}'
  AND bike_id IS NOT NULL AND rtn_dt IS NOT NULL AND rtn_dt >= rent_dt
""")

c.execute(f"""
CREATE TABLE g AS
SELECT * FROM (
  SELECT bike_id,
         lag(rtn_st) OVER w AS from_st, lag(rtn_dt) OVER w AS t_from,
         rent_st AS to_st, rent_dt AS t_to
  FROM t WINDOW w AS (PARTITION BY bike_id ORDER BY rent_dt, rtn_dt)
) WHERE t_from IS NOT NULL AND t_to >= TIMESTAMP '{start}' AND t_to >= t_from
""")

# 구간 안(반납 1시간 전 ~ 다음 대여)에 고장신고가 있었는지: 있으면 정비 수거로 본다
c.execute(f"""
CREATE TABLE out AS
WITH f AS (SELECT DISTINCT bike AS bike_id, reg_ts FROM '{ROOT}/data/processed/fail_std.parquet'
           WHERE reg_ts >= TIMESTAMP '{start}' - INTERVAL 31 DAY AND reg_ts < TIMESTAMP '{end}'),
rep AS (SELECT g.bike_id, g.t_to FROM g JOIN f ON g.bike_id = f.bike_id
        AND f.reg_ts BETWEEN g.t_from - INTERVAL 1 HOUR AND g.t_to GROUP BY ALL),
s AS (SELECT st_no, lat, lon FROM read_csv('{ROOT}/data/interim/station_master.csv'))
SELECT g.*, (g.from_st <> g.to_st) AS moved,
       date_diff('minute', g.t_from, g.t_to) / 60.0 AS gap_h,
       rep.bike_id IS NOT NULL AS had_report,
       2 * 6371000 * asin(sqrt(pow(sin(radians(b.lat - a.lat) / 2), 2)
         + cos(radians(a.lat)) * cos(radians(b.lat)) * pow(sin(radians(b.lon - a.lon) / 2), 2))) AS dist_m
FROM g
LEFT JOIN rep USING (bike_id, t_to)
LEFT JOIN s a ON a.st_no = g.from_st
LEFT JOIN s b ON b.st_no = g.to_st
""")
c.execute(f"COPY out TO '{HERE / 'out' / f'gaps_{period}.parquet'}'")
print(c.sql("""SELECT count(*) n, avg(moved::INT) moved_rate, avg(CASE WHEN moved THEN had_report::INT END) rep_rate_moved,
               avg(had_report::INT) rep_rate_all, count(*) FILTER (WHERE moved AND dist_m IS NULL) moved_no_coord FROM out"""))
