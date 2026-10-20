"""재배치 역산 10단계: 한 회수 대여소에서 실은 자전거를 한 곳에 다 내리나, 여러 곳에 나눠 내리나 (팀 프로젝트 탐색용 임시 파일)

무엇을: 회수 방문(02_visits, 대여소 + 시각 창) 하나에 속한 자전거들이 다음에 처음 대여된 대여소(= 배치 대여소)를 센다.
        한 곳이면 '한 곳에 모두', 여러 곳이면 '나눠 배치'. 배치처 간 거리와 배치 시각 창이 겹치는지(같은 운행으로 볼 수 있는지)도 본다.
주의: 시각 창이 겹치는 회수를 한 방문으로 묶었기 때문에 트럭 두 대의 회수가 합쳐지면 나눠 배치처럼 보인다.
      그래서 '창 1시간 이하 & 5~15대(트럭 한 대분)'인 엄격한 표본으로 따로 확인한다.
입력: out/gaps_2025.parquet, out/bike_pick·bike_drop.parquet, station_master.csv / 출력: 화면
"""
import os
from pathlib import Path

import duckdb

HERE = Path(__file__).parent
O = HERE / "out"
DAR = os.environ.get("DAREUNGI", "/Users/junyeop/dareungi")  # 따릉이 고장신고 프로젝트(대여이력 원본) 위치. README 참고
R = DAR
c = duckdb.connect()
c.execute(f"CREATE TABLE s AS SELECT st_no, lat, lon FROM read_csv('{R}/data/interim/station_master.csv') WHERE st_no IS NOT NULL")
c.execute(f"""CREATE TABLE b AS
SELECT g.bike_id, g.from_st, g.to_st, p.lo p_lo, p.hi p_hi, d.lo d_lo, d.hi d_hi
FROM '{O}/gaps_2025.parquet' g JOIN '{O}/bike_pick.parquet' p USING (bike_id, t_to) JOIN '{O}/bike_drop.parquet' d USING (bike_id, t_to)""")
# 회수 방문 × 배치 대여소
c.execute("""CREATE TABLE pd AS SELECT from_st, p_lo, p_hi, to_st, count(*) n, max(d_lo) d_lo, min(d_hi) d_hi FROM b GROUP BY ALL""")
c.execute("""CREATE TABLE pv AS
SELECT from_st, p_lo, p_hi, sum(n) n, count(*) k, max(n) / sum(n) top_share,
       date_diff('minute', p_lo, p_hi) / 60.0 width_h,
       max(d_lo) <= min(d_hi) AS one_trip                      -- 모든 배치처의 배치 창이 한 시점을 공유하면 한 번의 운행으로 볼 수 있다
FROM pd GROUP BY ALL""")

for name, cond in [("회수 방문 5대 이상 전체", "n >= 5"),
                   ("엄격: 창 1시간 이하 & 5~15대", "n BETWEEN 5 AND 15 AND width_h <= 1")]:
    print(f"\n== {name}")
    print(c.sql(f"""SELECT count(*) visits, sum(n) bikes,
      round(100 * avg((k = 1)::INT), 1) pct_one_dest, round(100 * avg((k = 2)::INT), 1) pct_two, round(100 * avg((k >= 3)::INT), 1) pct_three_plus,
      round(100 * sum(n) FILTER (WHERE k = 1) / sum(n), 1) bike_pct_one_dest, round(median(top_share), 2) med_top_share
      FROM pv WHERE {cond}"""))
    print("회수 대수별 배치처 수")
    print(c.sql(f"""SELECT CASE WHEN n <= 9 THEN 'a 5~9대' WHEN n <= 15 THEN 'b 10~15대' WHEN n <= 30 THEN 'c 16~30대' ELSE 'd 31대+' END size,
      count(*) visits, round(avg(k), 2) mean_dest, round(100 * avg((k = 1)::INT), 1) pct_one_dest, round(median(top_share), 2) med_top_share
      FROM pv WHERE {cond} GROUP BY 1 ORDER BY 1"""))

print("\n== 나눠 배치한 경우(엄격 표본, 배치처 2곳 이상)")
print(c.sql("""
WITH v AS (SELECT * FROM pv WHERE n BETWEEN 5 AND 15 AND width_h <= 1 AND k >= 2),
d AS (SELECT pd.*, v.k FROM pd JOIN v USING (from_st, p_lo, p_hi)),
pair AS (SELECT a.from_st, a.p_lo, a.p_hi,
         max(2 * 6371000 * asin(sqrt(pow(sin(radians(y.lat - x.lat) / 2), 2) + cos(radians(x.lat)) * cos(radians(y.lat)) * pow(sin(radians(y.lon - x.lon) / 2), 2)))) spread_m
         FROM d a JOIN d z ON a.from_st = z.from_st AND a.p_lo = z.p_lo AND a.p_hi = z.p_hi AND a.to_st < z.to_st
         JOIN s x ON x.st_no = a.to_st JOIN s y ON y.st_no = z.to_st GROUP BY ALL),
dist AS (SELECT d.from_st, d.p_lo, d.p_hi, median(2 * 6371000 * asin(sqrt(pow(sin(radians(y.lat - x.lat) / 2), 2) + cos(radians(x.lat)) * cos(radians(y.lat)) * pow(sin(radians(y.lon - x.lon) / 2), 2)))) from_m
         FROM d JOIN s x ON x.st_no = d.from_st JOIN s y ON y.st_no = d.to_st GROUP BY ALL)
SELECT count(*) visits, round(median(v.n), 1) med_bikes, round(median(v.n / v.k), 1) med_bikes_per_dest,
       round(median(v.top_share), 2) med_top_share, round(100 * avg(v.one_trip::INT), 1) pct_one_trip_window,
       round(median(spread_m)) med_spread_between_dests_m, round(median(from_m)) med_dist_from_pickup_m
FROM v JOIN pair USING (from_st, p_lo, p_hi) JOIN dist USING (from_st, p_lo, p_hi)"""))
print("배치처 수별 한 곳당 대수(엄격 표본)")
print(c.sql("""SELECT k, count(*) visits, round(median(n)) med_bikes, round(median(n * 1.0 / k), 1) per_dest, round(median(top_share), 2) top_share
               FROM pv WHERE n BETWEEN 5 AND 15 AND width_h <= 1 GROUP BY k ORDER BY k LIMIT 6"""))

# 반대 방향: 한 배치 대여소가 여러 회수 대여소에서 모아 받나
c.execute("CREATE TABLE dp AS SELECT to_st, d_lo, d_hi, count(DISTINCT from_st) k, count(*) n, date_diff('minute', d_lo, d_hi) / 60.0 width_h FROM b GROUP BY to_st, d_lo, d_hi")
print("\n== 반대 방향: 배치 방문 하나가 몇 곳에서 모아 왔나(창 1시간 이하 & 5~15대)")
print(c.sql("""SELECT count(*) visits, round(100 * avg((k = 1)::INT), 1) pct_one_source, round(100 * avg((k = 2)::INT), 1) pct_two,
               round(100 * avg((k >= 3)::INT), 1) pct_three_plus FROM dp WHERE n BETWEEN 5 AND 15 AND width_h <= 1"""))
