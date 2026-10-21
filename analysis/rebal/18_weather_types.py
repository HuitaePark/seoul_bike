"""재배치 역산 18단계: 날씨 유형별 대여·반납 모양과 '어디에 쌓이나' (팀 프로젝트 탐색용 임시 파일)

무엇을: 2024~2025년 날마다 시간대별 강수(wx_h)로 비의 시점을 나누고(오후부터 비 / 오전 비 후 갬 / 종일 비) 눈·한파·폭염을 더한다.
        같은 해·같은 달·같은 요일 유형의 보통 날 중앙값과 비교해 ① 시간대별 대여 ② 대여소 유형별 순반납(반납 − 대여, +면 쌓임)
        ③ 트럭 회수(당일·다음 날)를 본다.
날씨 기준(임시): 오전 = 6~11시, 오후 = 12~20시 강수 합. 오후부터 비 = 오전 < 0.5mm & 오후 ≥ 3mm, 오전 비 후 갬 = 오전 ≥ 3mm & 오후 < 0.5mm,
        종일 비 = 둘 다 ≥ 3mm, 눈 = 적설 > 0, 한파 = 일평균 −4℃ 이하, 폭염 = 최고 33℃ 이상, 보통 = 하루 강수 1mm 미만이고 위에 해당 없음.
대여소 유형(2025 기준 station_roles): 역 이름 / 업무형(아침 도착 위주 군집) / 주거형(아침 출발·저녁 도착 군집) / 언덕 위(자치구 중앙값 +10m 이상)
입력: 대여이력 2024·2025, daily_master_v2, out/wx_h.parquet, out/visits_2024·2025, out/station_roles.csv / 출력: 화면
"""
import os
from pathlib import Path

import duckdb

HERE = Path(__file__).parent
O = HERE / "out"
DAR = os.environ.get("DAREUNGI", "/Users/junyeop/dareungi")  # 따릉이 고장신고 프로젝트(대여이력 원본) 위치. README 참고
R = DAR
c = duckdb.connect()
c.execute(f"SET memory_limit='8GB'; SET temp_directory='{HERE / 'tmp'}'")
c.execute(f"""CREATE TABLE wx AS
WITH h AS (SELECT ts::DATE d, sum(rain_mm) FILTER (WHERE hour(ts) BETWEEN 6 AND 11) am, sum(rain_mm) FILTER (WHERE hour(ts) BETWEEN 12 AND 20) pm
           FROM '{O}/wx_h.parquet' GROUP BY 1)
SELECT m.date::DATE d, year(m.date) yr, month(m.date) mo, m.is_off::BOOLEAN is_off, h.am, h.pm,
  CASE WHEN snow_cm > 0 THEN 'a 눈'
       WHEN h.am < 0.5 AND h.pm >= 3 THEN 'b 오후부터 비' WHEN h.am >= 3 AND h.pm < 0.5 THEN 'c 오전 비 후 갬'
       WHEN h.am >= 3 AND h.pm >= 3 THEN 'd 종일 비' WHEN rain_mm >= 1 THEN 'x 기타 비'
       WHEN tavg <= -4 THEN 'e 한파' WHEN tmax >= 33 THEN 'f 폭염' ELSE 'g 보통' END wx
FROM '{R}/data/processed/daily_master_v2.parquet' m JOIN h ON h.d = m.date::DATE
WHERE year(m.date) IN (2024, 2025) AND NOT m.no_service""")
c.execute(f"""CREATE TABLE grp AS SELECT st_no,
  CASE WHEN near_sub THEN '역 이름' WHEN cl = 1 THEN '업무형' WHEN cl = 4 THEN '주거형' WHEN rel_elev >= 10 THEN '언덕 위' END g
FROM read_csv('{O}/station_roles.csv')""")
P = f"read_parquet(['{R}/data/processed/rent/y=2024/*/*.parquet', '{R}/data/processed/rent/y=2025/*/*.parquet'])"
c.execute(f"""CREATE TABLE day AS
WITH r AS (SELECT rent_dt::DATE d, count(*) rent,
             count(*) FILTER (WHERE hour(rent_dt) BETWEEN 7 AND 9) r_am, count(*) FILTER (WHERE hour(rent_dt) BETWEEN 10 AND 16) r_mid,
             count(*) FILTER (WHERE hour(rent_dt) BETWEEN 17 AND 19) r_pm, count(*) FILTER (WHERE hour(rent_dt) >= 20) r_night
           FROM {P} GROUP BY 1),
e AS (SELECT rtn_st st, rent_dt::DATE d, 1 x FROM {P} WHERE rtn_st IS NOT NULL UNION ALL SELECT rent_st, rent_dt::DATE, -1 FROM {P} WHERE rtn_st IS NOT NULL),
gn AS (SELECT d, sum(x) FILTER (WHERE g = '역 이름') n_sub, sum(x) FILTER (WHERE g = '업무형') n_off,
              sum(x) FILTER (WHERE g = '주거형') n_res, sum(x) FILTER (WHERE g = '언덕 위') n_hill FROM e JOIN grp ON grp.st_no = e.st GROUP BY 1),
v AS (SELECT (lo + (hi - lo) / 2)::DATE d, sum(n) FILTER (WHERE kind = 'pick') pick
      FROM read_parquet(['{O}/visits_2024.parquet', '{O}/visits_2025.parquet']) GROUP BY 1)
SELECT wx.*, r.*, gn.* EXCLUDE (d), coalesce(v.pick, 0) pick, lead(coalesce(v.pick, 0)) OVER (ORDER BY wx.d) pick_next,
       lead(r.rent) OVER (ORDER BY wx.d) rent_next
FROM wx JOIN r USING (d) JOIN gn USING (d) LEFT JOIN v USING (d)""")
c.execute("""CREATE TABLE rel AS
WITH b AS (SELECT yr, mo, is_off, median(rent) b_rent, median(r_am) b_am, median(r_mid) b_mid, median(r_pm) b_pm, median(r_night) b_night,
                  median(pick) b_pick, median(n_sub) b_sub, median(n_off) b_off, median(n_res) b_res, median(n_hill) b_hill
           FROM day WHERE wx = 'g 보통' GROUP BY ALL)
SELECT day.*, rent / b_rent x_rent, r_am / b_am x_am, r_mid / b_mid x_mid, r_pm / b_pm x_pm, r_night / b_night x_night,
       pick / b_pick x_pick, pick_next / b_pick x_pick_next, rent_next / b_rent x_rent_next,
       n_sub - b_sub d_sub, n_off - b_off d_off, n_res - b_res d_res, n_hill - b_hill d_hill
FROM day JOIN b USING (yr, mo, is_off)""")
print("== 날씨 유형별(보통 날 = 1.00, 같은 해·달·요일 유형)")
print(c.sql("""SELECT wx, count(*) n_days, round(avg(x_rent), 2) 대여, round(avg(x_am), 2) "07~10시", round(avg(x_mid), 2) "10~17시",
  round(avg(x_pm), 2) "17~20시", round(avg(x_night), 2) "20시~", round(avg(x_pick), 2) 트럭회수, round(avg(x_pick_next), 2) "다음날 회수",
  round(avg(x_rent_next), 2) "다음날 대여" FROM rel WHERE wx <> 'x 기타 비' GROUP BY 1 ORDER BY 1""").df().to_string(index=False))
print("\n== 대여소 유형별 하루 순반납의 변화(대, 보통 날 대비. +면 평소보다 더 쌓임, −면 더 빠짐)")
print(c.sql("""SELECT wx, round(avg(d_sub)) 역이름, round(avg(d_off)) 업무형, round(avg(d_res)) 주거형, round(avg(d_hill)) 언덕위
  FROM rel WHERE wx <> 'x 기타 비' GROUP BY 1 ORDER BY 1""").df().to_string(index=False))
print("\n참고: 보통 날 대여소 유형별 하루 순반납(평일 중앙값)")
print(c.sql("""SELECT round(median(n_sub)) 역이름, round(median(n_off)) 업무형, round(median(n_res)) 주거형, round(median(n_hill)) 언덕위
  FROM day WHERE wx = 'g 보통' AND NOT is_off"""))
print(c.sql("SELECT count(*) FROM grp GROUP BY g").fetchall(), c.sql("SELECT g, count(*) FROM grp GROUP BY 1").fetchall())
