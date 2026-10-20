"""재배치 역산 12단계: 비·눈·한파·폭염 날에도 평소처럼 재배치하나, 그런 날에도 회수가 필요할 만큼 쌓이나 (팀 프로젝트 탐색용 임시 파일)

무엇을: 2025년 날마다 대여량, 트럭 회수·배치 대수(02_visits), 쌓이는 양(대여소별 하루 순반납의 양수 합 = 회수가 필요한 양)을 세고
        날씨로 날을 나눈다. 계절·요일 효과를 빼려고 같은 달·같은 요일 유형(평일/주말·공휴일)의 '보통 날' 중앙값을 1로 놓고 비율로 비교한다.
날씨 분류(우선순위 순, 임시 기준): 눈(적설 > 0) / 비 10mm 이상 / 비 1~10mm / 한파(일평균 −4℃ 이하) / 폭염(최고 33℃ 이상) / 보통
입력: out/gaps_2025.parquet, out/visits_2025.parquet, 본 프로젝트 대여이력·daily_master_v2(읽기만) / 출력: 화면
"""
import os
from pathlib import Path

import duckdb

HERE = Path(__file__).parent
O = HERE / "out"
DAR = os.environ.get("DAREUNGI", "/Users/junyeop/dareungi")  # 따릉이 고장신고 프로젝트(대여이력 원본) 위치. README 참고
R = DAR
c = duckdb.connect()
c.execute(f"""CREATE TABLE w AS SELECT date::DATE d, rain_mm, snow_cm, tavg, tmax, is_off::BOOLEAN is_off, month(date) mo,
  CASE WHEN snow_cm > 0 THEN 'a 눈' WHEN rain_mm >= 10 THEN 'b 비 10mm+' WHEN rain_mm >= 1 THEN 'c 비 1~10mm'
       WHEN tavg <= -4 THEN 'd 한파' WHEN tmax >= 33 THEN 'e 폭염' ELSE 'f 보통' END wx
FROM '{R}/data/processed/daily_master_v2.parquet' WHERE year(date) = 2025 AND NOT no_service""")
c.execute(f"""CREATE TABLE day AS
WITH r AS (SELECT rent_dt::DATE d, count(*) rent FROM read_parquet('{R}/data/processed/rent/y=2025/*/*.parquet') GROUP BY 1),
e AS (SELECT rtn_st st, rent_dt::DATE d, 1 x FROM read_parquet('{R}/data/processed/rent/y=2025/*/*.parquet') WHERE rtn_st IS NOT NULL
      UNION ALL SELECT rent_st, rent_dt::DATE, -1 FROM read_parquet('{R}/data/processed/rent/y=2025/*/*.parquet') WHERE rtn_st IS NOT NULL),
nd AS (SELECT d, sum(greatest(net, 0)) need, count(*) FILTER (WHERE net >= 10) n_st_10 FROM (SELECT st, d, sum(x) net FROM e GROUP BY ALL) GROUP BY 1),
v AS (SELECT (lo + (hi - lo) / 2)::DATE d, sum(n) FILTER (WHERE kind = 'pick') pick, sum(n) FILTER (WHERE kind = 'drop') drp FROM '{O}/visits_2025.parquet' GROUP BY 1)
SELECT w.*, rent, need, n_st_10, coalesce(pick, 0) pick, coalesce(drp, 0) drp FROM w JOIN r USING (d) JOIN nd USING (d) LEFT JOIN v USING (d)""")
# 같은 달·같은 요일 유형의 보통 날 중앙값 = 1
c.execute("""CREATE TABLE rel AS
WITH base AS (SELECT mo, is_off, median(rent) b_rent, median(need) b_need, median(pick) b_pick, median(n_st_10) b_st FROM day WHERE wx = 'f 보통' GROUP BY ALL)
SELECT day.*, rent / b_rent r_rent, need / b_need r_need, pick / b_pick r_pick, n_st_10 / b_st r_st,
       lag(wx) OVER (ORDER BY d) prev_wx
FROM day JOIN base USING (mo, is_off)""")
print("== 날씨별(보통 날 = 1.00, 같은 달·같은 요일 유형 기준)")
print(c.sql("""SELECT wx, count(*) n_days, round(avg(r_rent), 2) 대여, round(avg(r_need), 2) 쌓이는양, round(avg(r_st), 2) "10대+쌓인 대여소",
  round(avg(r_pick), 2) 트럭회수, round(median(pick / need), 2) "회수/쌓이는양" FROM rel GROUP BY 1 ORDER BY 1""").df().to_string(index=False))
print("\n== 절대값(하루 평균)")
print(c.sql("""SELECT wx, round(avg(rent)) 대여, round(avg(need)) 쌓이는양, round(avg(n_st_10)) "10대+쌓인 대여소", round(avg(pick)) 트럭회수
  FROM day GROUP BY 1 ORDER BY 1""").df().to_string(index=False))
print("\n== 궂은 날 다음 날(그 자체는 보통 날)")
print(c.sql("""SELECT CASE WHEN prev_wx IN ('a 눈', 'b 비 10mm+') THEN '눈·큰비 다음 날' ELSE '보통 날 다음 보통 날' END k, count(*) n_days,
  round(avg(r_rent), 2) 대여, round(avg(r_need), 2) 쌓이는양, round(avg(r_pick), 2) 트럭회수
  FROM rel WHERE wx = 'f 보통' GROUP BY 1"""))
print("\n== 큰비(10mm+) 날 목록 상위(강수 많은 순)")
print(c.sql("""SELECT d, dayname(d) dow, rain_mm, round(r_rent, 2) 대여, round(r_need, 2) 쌓이는양, round(r_pick, 2) 트럭회수 FROM rel
  WHERE wx = 'b 비 10mm+' ORDER BY rain_mm DESC LIMIT 8""").df().to_string(index=False))
