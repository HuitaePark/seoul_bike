# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: 활동 구역 17개를 요약하고, 개발 기준 구역 후보를 규모·역할·경계 이동·행사 시나리오로 비교한다.

"""활동 구역 요약과 개발 기준 구역 비교 (M1).

왜: 계획 단위는 역산 활동 구역 17개 전체다(2026-10-20 결정). 다만 개발과 03a·03b 통과는 한 구역으로 먼저 하므로,
    PRD 규모(대여소 100~200곳, 트럭 3~5대)에 맞으면서 S1 불꽃축제를 재현할 수 있는 구역을 고른다.
입력: analysis/out/stations.csv (stations.py),
      dareungi 2025 대여이력 Parquet (경계 이동, 행사일 대여)
출력: 화면 (docs/data/regions.md에 옮김)
실행: /Users/junyeop/dareungi/.venv/bin/python analysis/regions.py
"""
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DAR = Path("/Users/junyeop/dareungi")
CANDIDATES = ("강북-02", "강남-05")
FIREWORKS = "2025-09-27"  # 서울세계불꽃축제(토). 여의도 일대 대여·반납 중지 9/26 19시 ~ 9/28 9시

c = duckdb.connect()
c.execute(f"CREATE TABLE al AS SELECT * FROM '{ROOT}/analysis/out/stations.csv'")
c.execute(f"CREATE TABLE st AS SELECT * FROM al WHERE terr IN {CANDIDATES}")
rent = (DAR / "data/processed/rent/y=2025/*/*.parquet").as_posix()
c.execute(f"CREATE VIEW tr AS SELECT rent_dt, rent_st, rtn_st FROM read_parquet('{rent}')")


def show(title, sql):
    print(f"\n== {title}")
    print(c.sql(sql).df().to_string(index=False))


show("활동 구역 17개 (대여소 = 지금 운영 중인 곳, 붙인곳 = 가장 가까운 구역에 붙인 곳, 이용·재배치는 2025-04~10 하루 평균)", """
SELECT coalesce(terr, '구역 없음') AS terr, count(*) FILTER (WHERE active) AS 대여소,
       count(*) FILTER (WHERE active AND terr_src = 'nearest') AS 붙인곳, count(*) FILTER (WHERE NOT active) AS 미운영,
       string_agg(DISTINCT district, '·' ORDER BY district) AS 자치구,
       round(sum(rentals_d)) AS 하루대여, round(sum(reb_in_d)) AS 하루배치, round(sum(reb_out_d)) AS 하루회수,
       count(*) FILTER (WHERE role = '고정 회수') AS 고정회수, count(*) FILTER (WHERE role = '고정 배치') AS 고정배치
FROM al GROUP BY 1 ORDER BY 1""")

show("구역을 넘는 대여 (2025-04~10, 전 구역 시뮬레이터에서는 내부 이동)", """
SELECT round(100 * avg((a.terr = b.terr)::INT), 1) AS 같은구역pct, round(100 * avg((a.region = b.region)::INT), 1) AS 같은강쪽pct,
       round(count(*) / 214) AS 하루대여
FROM tr r JOIN al a ON a.st_no = r.rent_st JOIN al b ON b.st_no = r.rtn_st
WHERE r.rent_dt >= '2025-04-01' AND r.rent_dt < '2025-11-01'""")


show("규모와 역할 (이용·재배치는 2025-04~10 하루 평균)", """
SELECT terr, count(*) FILTER (WHERE active) AS 운영대여소, string_agg(DISTINCT district, '·') AS 자치구,
       sum(hold_num) FILTER (WHERE active) AS 거치대,
       round(sum(rentals_d)) AS 하루대여, round(sum(reb_in_d)) AS 하루배치, round(sum(reb_out_d)) AS 하루회수,
       count(*) FILTER (WHERE role = '고정 회수') AS 고정회수, count(*) FILTER (WHERE role = '고정 배치') AS 고정배치,
       count(*) FILTER (WHERE active AND addr LIKE '%여의%') AS 여의도, round(max(elev_m) - min(elev_m)) AS 고도폭m
FROM st GROUP BY 1 ORDER BY 1""")

for col, name in (("reb_out_d", "회수"), ("reb_in_d", "배치")):
    show(f"하루 {name} 상위 5곳", f"""
    SELECT terr, st_no, st_name, {col} AS 하루대수, role FROM (
      SELECT *, row_number() OVER (PARTITION BY terr ORDER BY {col} DESC) AS k FROM st) WHERE k <= 5 ORDER BY terr, k""")

show("경계를 넘는 대여 (2025-04~10, 시뮬레이터 경계 처리용)", """
WITH t AS (SELECT r.rent_st, r.rtn_st, a.terr AS ta, b.terr AS tb FROM tr r
           LEFT JOIN st a ON a.st_no = r.rent_st LEFT JOIN st b ON b.st_no = r.rtn_st
           WHERE r.rent_dt >= '2025-04-01' AND r.rent_dt < '2025-11-01' AND r.rtn_st IS NOT NULL)
SELECT x.terr,
       round(count(*) FILTER (WHERE ta = x.terr OR tb = x.terr) / 214) AS 하루관련대여,
       round(100 * count(*) FILTER (WHERE ta = x.terr AND tb = x.terr) / count(*) FILTER (WHERE ta = x.terr), 1) AS 대여중안에서반납pct,
       round(100 * count(*) FILTER (WHERE ta = x.terr AND tb = x.terr) / count(*) FILTER (WHERE tb = x.terr), 1) AS 반납중안에서출발pct,
       round((count(*) FILTER (WHERE tb = x.terr AND ta IS DISTINCT FROM x.terr)
            - count(*) FILTER (WHERE ta = x.terr AND tb IS DISTINCT FROM x.terr)) / 214, 1) AS 하루순유입
FROM t, (SELECT DISTINCT terr FROM st) x GROUP BY 1 ORDER BY 1""")

show(f"불꽃축제 {FIREWORKS} 대여 (같은 해 9~10월 다른 토요일 중앙값 대비)", f"""
WITH d AS (SELECT s.terr, r.rent_dt::DATE AS dt, count(*) FILTER (WHERE s.addr LIKE '%여의%') AS yeouido, count(*) AS total
           FROM tr r JOIN st s ON s.st_no = r.rent_st
           WHERE r.rent_dt >= '2025-09-01' AND r.rent_dt < '2025-11-01' AND dayofweek(r.rent_dt) = 6 GROUP BY 1, 2)
SELECT terr, max(total) FILTER (WHERE dt = '{FIREWORKS}') AS 당일대여, median(total) FILTER (WHERE dt <> '{FIREWORKS}') AS 다른토요일,
       max(yeouido) FILTER (WHERE dt = '{FIREWORKS}') AS 당일여의도, median(yeouido) FILTER (WHERE dt <> '{FIREWORKS}') AS 다른토요일여의도
FROM d GROUP BY 1 ORDER BY 1""")
