"""재배치 역산 5단계: 출퇴근 외 — 한강·공원 대여소와 축제 날에도 재배치가 일어나는지. (팀 프로젝트 탐색용 임시 파일)

무엇을: 대여소를 한강(공원 출입구 이름) / 공원·여가(주말 대여 비중 33% 이상, 한강 제외) / 나머지로 나누고
        ① 평일·주말 하루 대여와 재배치 반입·반출 ② 주말 시각별 회수·배치 방문 ③ 이용이 평소의 2배를 넘은 날(행사 후보)의 재배치를 본다.
입력: out/gaps_2025.parquet, out/visits_2025.parquet, station_master.csv / 출력: 화면
"""
from pathlib import Path

import duckdb

HERE = Path(__file__).parent
O = HERE / "out"
c = duckdb.connect()
c.execute(f"""
CREATE TABLE grp AS
WITH w AS (SELECT to_st st, avg((isodow(t_to) >= 6)::INT) we_share, count(*) n FROM '{O}/gaps_2025.parquet' GROUP BY 1)
SELECT s.st_no, s.st_name, s.district,
  CASE WHEN regexp_matches(st_name, '한강공원|나들목|선착장|마리나|유원지역|여의나루|노들섬') AND st_name NOT LIKE '나들목공원%' THEN '한강'
       WHEN we_share >= 0.33 AND n >= 2000 THEN '공원·여가'
       ELSE '나머지' END AS g
FROM read_csv('/Users/junyeop/dareungi/data/interim/station_master.csv') s JOIN w ON w.st = s.st_no""")
print(c.sql("SELECT g, count(*) n_st FROM grp GROUP BY 1"))

# 날짜별 그룹 대여·재배치 반입·반출 (재배치는 방문 창 중간 시각의 날짜)
c.execute(f"""
CREATE TABLE day AS
WITH r AS (SELECT to_st st, t_to::DATE dt, count(*) rent FROM '{O}/gaps_2025.parquet' GROUP BY ALL),
v AS (SELECT st, (lo + (hi - lo) / 2)::DATE dt, sum(n) FILTER (WHERE kind = 'drop') d_in, sum(n) FILTER (WHERE kind = 'pick') d_out
      FROM '{O}/visits_2025.parquet' GROUP BY ALL)
SELECT g, dt, isodow(dt) >= 6 we, sum(rent) rent, coalesce(sum(d_in), 0) reb_in, coalesce(sum(d_out), 0) reb_out
FROM r FULL JOIN v USING (st, dt) JOIN grp ON grp.st_no = st WHERE dt >= '2025-04-01' AND dt < '2025-11-01' GROUP BY ALL""")
print("\n== 4~10월 하루 평균(그룹 합계)")
print(c.sql("""SELECT g, CASE WHEN we THEN '주말' ELSE '평일' END d, round(avg(rent)) rent_day, round(avg(reb_in)) reb_in_day,
  round(avg(reb_out)) reb_out_day, round(100 * avg(reb_in) / avg(rent), 1) pct_in_of_rent FROM day GROUP BY ALL ORDER BY 1, 2"""))

print("\n== 한강·공원 그룹 시각별 재배치(방문 창 1시간 이하, 4~10월, 하루 평균 대수)")
print(c.sql(f"""
SELECT hour(lo + (hi - lo) / 2) h,
  round(sum(n) FILTER (WHERE kind = 'drop' AND we AND g <> '나머지') / 61.0, 1) we_drop,
  round(sum(n) FILTER (WHERE kind = 'pick' AND we AND g <> '나머지') / 61.0, 1) we_pick,
  round(sum(n) FILTER (WHERE kind = 'drop' AND NOT we AND g <> '나머지') / 153.0, 1) wd_drop,
  round(sum(n) FILTER (WHERE kind = 'pick' AND NOT we AND g <> '나머지') / 153.0, 1) wd_pick
FROM (SELECT *, isodow(lo) >= 6 we FROM '{O}/visits_2025.parquet') v JOIN grp ON grp.st_no = v.st
WHERE width_h <= 1 AND lo >= '2025-04-01' AND lo < '2025-11-01' GROUP BY 1 HAVING h BETWEEN 6 AND 23 ORDER BY 1""").df().to_string(index=False))

print("\n== 한강 그룹: 이용이 같은 요일 유형 중앙값의 1.8배 넘은 날(행사 후보) vs 평소")
print(c.sql("""
WITH m AS (SELECT we, median(rent) med_rent, median(reb_in) med_in, median(reb_out) med_out FROM day WHERE g = '한강' GROUP BY 1)
SELECT dt, dayname(dt) dow, rent, round(rent / med_rent, 1) x_rent, reb_in, med_in, reb_out, med_out
FROM day JOIN m USING (we) WHERE g = '한강' AND rent >= 1.8 * med_rent ORDER BY rent / med_rent DESC LIMIT 12""").df().to_string(index=False))
