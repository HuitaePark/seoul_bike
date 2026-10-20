"""재배치 역산 13단계: 트럭이 회수하러 가는 사이 이용자가 빌려 가서 재고가 줄어드는가 (팀 프로젝트 탐색용 임시 파일)

무엇을: ① 회수 방문(창 1시간 이하, 5대 이상)의 창 시작 직전 60분·30분 동안 그 대여소에서 대여로 나간 대수와 순변화(반납 − 대여)
        ② 회수 대여소(4~10월 하루 순반출 2대 이상인 '회수처') 전체에서, 운영 시간(07~21시) 아무 60분 동안 재고가 얼마나 줄 수 있나
        ③ 2025-05 복원 재고로: 회수 직후 2시간 안에 그 대여소가 비어 버린(재고 0) 비율 = 너무 많이 가져간 경우
입력: out/gaps_2025.parquet, out/visits_2025.parquet, out/bike_pick·bike_drop.parquet, out/station_roles.csv, 대여이력(읽기만)
출력: 화면
"""
from pathlib import Path

import duckdb

HERE = Path(__file__).parent
O = HERE / "out"
R = "/Users/junyeop/dareungi"
c = duckdb.connect()
c.execute(f"SET memory_limit='8GB'; SET temp_directory='{HERE / 'tmp'}'")
P = f"read_parquet('{R}/data/processed/rent/y=2025/*/*.parquet')"
c.execute(f"CREATE TABLE ev AS SELECT rent_st st, rent_dt ts, -1 x FROM {P} UNION ALL SELECT rtn_st, rtn_dt, 1 FROM {P} WHERE rtn_st IS NOT NULL")
c.execute(f"CREATE TABLE pv AS SELECT st, lo, hi, n FROM '{O}/visits_2025.parquet' WHERE kind = 'pick' AND n >= 5 AND width_h <= 1")

print("== ① 회수 직전 그 대여소에서 빠져나간 자전거(회수 방문 창 1시간 이하·5대 이상, 2025)")
print(c.sql("""
WITH x AS (SELECT pv.st, pv.lo, pv.n,
  count(*) FILTER (WHERE ev.x = -1 AND ev.ts >= pv.lo - INTERVAL 60 MINUTE) rent60,
  coalesce(sum(ev.x) FILTER (WHERE ev.ts >= pv.lo - INTERVAL 60 MINUTE), 0) net60,
  coalesce(sum(ev.x) FILTER (WHERE ev.ts >= pv.lo - INTERVAL 30 MINUTE), 0) net30
  FROM pv LEFT JOIN ev ON ev.st = pv.st AND ev.ts >= pv.lo - INTERVAL 60 MINUTE AND ev.ts < pv.lo GROUP BY ALL)
SELECT count(*) visits, median(n) 회수대수, median(rent60) "직전60분 대여", quantile_cont(rent60, 0.9) "대여 p90",
  round(100 * avg((net60 <= -3)::INT), 1) "60분 순감소 3대+%", round(100 * avg((net60 <= -5)::INT), 1) "5대+%",
  round(100 * avg((net30 <= -3)::INT), 1) "30분 순감소 3대+%", round(100 * avg((-net60 >= n * 0.3)::INT), 1) "회수량의 30%+ 감소%"
FROM x""").df().T.to_string())
print("시간대별(회수 시작 시각)")
print(c.sql("""
WITH x AS (SELECT pv.st, pv.lo, pv.n, coalesce(sum(ev.x), 0) net60, count(*) FILTER (WHERE ev.x = -1) rent60
  FROM pv LEFT JOIN ev ON ev.st = pv.st AND ev.ts >= pv.lo - INTERVAL 60 MINUTE AND ev.ts < pv.lo GROUP BY ALL)
SELECT hour(lo) h, count(*) visits, median(rent60) "직전60분 대여", round(100 * avg((net60 <= -3)::INT), 1) "순감소 3대+%" FROM x
WHERE hour(lo) BETWEEN 6 AND 21 GROUP BY 1 ORDER BY 1""").df().T.to_string())

print("\n== ② 회수처 대여소의 아무 60분(07~21시 정시 시작, 4~10월): 재고가 3대·5대 이상 줄어든 비율")
print(c.sql(f"""
WITH src AS (SELECT st_no FROM read_csv('{O}/station_roles.csv') WHERE reb_net <= -2),
h AS (SELECT st, date_trunc('hour', ts) hr, sum(x) net, count(*) FILTER (WHERE x = -1) rent FROM ev
      WHERE st IN (SELECT st_no FROM src) AND month(ts) BETWEEN 4 AND 10 AND hour(ts) BETWEEN 7 AND 21 GROUP BY ALL)
SELECT CASE WHEN hour(hr) BETWEEN 7 AND 9 THEN 'a 07~10시' WHEN hour(hr) BETWEEN 10 AND 16 THEN 'b 10~17시' ELSE 'c 17~22시' END band,
  round(100 * avg((net <= -3)::INT), 1) "3대+ 감소%", round(100 * avg((net <= -5)::INT), 1) "5대+ 감소%", round(avg(rent), 1) "시간당 대여"
FROM h GROUP BY 1 ORDER BY 1"""))

# ③ 2025-05 재고 복원(04_stock과 같은 방식)
A, B = "2025-05-01", "2025-06-01"
c.execute(f"""CREATE TABLE iv AS
WITH g AS (SELECT * FROM '{O}/gaps_2025.parquet' WHERE t_to >= '{A}' AND t_from < '{B}'),
p AS (SELECT bike_id, t_to, lo + (hi - lo) / 2 AS m FROM '{O}/bike_pick.parquet'),
d AS (SELECT bike_id, t_to, lo + (hi - lo) / 2 AS m FROM '{O}/bike_drop.parquet')
SELECT from_st AS st, t_from AS s, t_to AS e FROM g WHERE NOT moved
UNION ALL SELECT g.from_st, g.t_from, p.m FROM g JOIN p USING (bike_id, t_to)
UNION ALL SELECT g.to_st, d.m, g.t_to FROM g JOIN d USING (bike_id, t_to)""")
c.execute("""CREATE TABLE ser AS SELECT st, ts, sum(dx) OVER (PARTITION BY st ORDER BY ts, dx ROWS UNBOUNDED PRECEDING) stock
FROM (SELECT st, s ts, 1 dx FROM iv UNION ALL SELECT st, e, -1 FROM iv)""")
print("\n== ③ 회수 직후 2시간 안에 재고 0이 된 비율(2025-05, 회수 방문 5대 이상)")
print(c.sql(f"""
WITH v AS (SELECT st, lo, hi, n, lo + (hi - lo) / 2 m FROM '{O}/visits_2025.parquet' WHERE kind = 'pick' AND n >= 5 AND lo >= '{A}' AND hi < '{B}'),
after AS (SELECT v.st, v.m, v.n, min(ser.stock) min_after FROM v JOIN ser ON ser.st = v.st AND ser.ts > v.m AND ser.ts <= v.m + INTERVAL 2 HOUR GROUP BY ALL),
before AS (SELECT v.st, v.m, ser.stock stock_before FROM v ASOF JOIN ser ON ser.st = v.st AND v.lo >= ser.ts)
SELECT count(*) visits, round(100 * avg((min_after = 0)::INT), 1) "2시간 안 재고 0%", round(100 * avg((min_after <= 2)::INT), 1) "2대 이하%",
  median(stock_before) "회수 직전 재고", median(n) "회수 대수"
FROM after JOIN before USING (st, m)"""))
