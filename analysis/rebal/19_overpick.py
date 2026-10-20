"""재배치 역산 19단계: 너무 많이 가져가 대여소가 빈 뒤, 실제로 빌리려던 수요가 있었나 (팀 프로젝트 탐색용 임시 파일)

무엇을: 2025-05, 2025-09 재고를 복원(04_stock 방식)하고, 5대 이상 회수 뒤 2시간 안에 재고 0이 된 경우('과다 회수')를 고른다.
        ① 회수 뒤 3시간 동안 빈 시간(분) ② 예상 놓친 대여 = 그 대여소·그 시간대·같은 요일 유형의 평소 시간당 대여 × 빈 시간
        ③ 직접 증거: 빈 동안 반납된 자전거가 10분 안에 바로 대여된 횟수(기다리던 사람이 있었다는 뜻)
        ④ 비교: 같은 달 회수가 없었는데 빈 경우(대여소×시간) 1시간당 '10분 안 재대여' 횟수
입력: out/gaps_2025.parquet, out/bike_pick·bike_drop.parquet, out/visits_2025.parquet, 대여이력(읽기만) / 출력: 화면
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
for A, B in [("2025-05-01", "2025-06-01"), ("2025-09-01", "2025-10-01")]:
    c.execute("DROP TABLE IF EXISTS iv; DROP TABLE IF EXISTS ser")
    c.execute(f"""CREATE TABLE iv AS
    WITH g AS (SELECT * FROM '{O}/gaps_2025.parquet' WHERE t_to >= '{A}' AND t_from < '{B}'),
    p AS (SELECT bike_id, t_to, lo + (hi - lo) / 2 AS m FROM '{O}/bike_pick.parquet'),
    d AS (SELECT bike_id, t_to, lo + (hi - lo) / 2 AS m FROM '{O}/bike_drop.parquet')
    SELECT from_st AS st, t_from AS s, t_to AS e, 'stay' k FROM g WHERE NOT moved
    UNION ALL SELECT g.from_st, g.t_from, p.m, 'stay' FROM g JOIN p USING (bike_id, t_to)
    UNION ALL SELECT g.to_st, d.m, g.t_to, 'drop' FROM g JOIN d USING (bike_id, t_to)""")
    # 사건: +1 반납(또는 배치 도착), −1 대여(또는 회수). 다음 사건 시각·종류도 붙인다
    c.execute("""CREATE TABLE ser AS
    SELECT st, ts, dx, kind, sum(dx) OVER w stock, lead(ts) OVER w ts_next, lead(dx) OVER w dx_next
    FROM (SELECT st, s ts, 1 dx, 'in' kind FROM iv UNION ALL SELECT st, e, -1, 'out' FROM iv)
    WINDOW w AS (PARTITION BY st ORDER BY ts, dx ROWS UNBOUNDED PRECEDING)""")
    c.execute(f"""CREATE OR REPLACE TABLE rate AS
    SELECT rent_st st, hour(rent_dt) h, isodow(rent_dt) >= 6 we, count(*) / any_value(nd) per_h   -- 그 달 같은 요일 유형 날수로 나눈 시간당 평균 대여
    FROM read_parquet('{R}/data/processed/rent/y=2025/*/*.parquet'),
         (SELECT count(*) FILTER (WHERE isodow(d) < 6) nwd, count(*) FILTER (WHERE isodow(d) >= 6) nwe FROM (SELECT DISTINCT rent_dt::DATE d FROM read_parquet('{R}/data/processed/rent/y=2025/*/*.parquet') WHERE rent_dt >= '{A}' AND rent_dt < '{B}')) n,
         LATERAL (SELECT CASE WHEN isodow(rent_dt) >= 6 THEN n.nwe ELSE n.nwd END nd)
    WHERE rent_dt >= '{A}' AND rent_dt < '{B}' GROUP BY ALL""")
    c.execute(f"""CREATE OR REPLACE TABLE pk AS SELECT st, lo + (hi - lo) / 2 m, n FROM '{O}/visits_2025.parquet'
                  WHERE kind = 'pick' AND n >= 5 AND lo >= '{A}' AND hi < '{B}'""")
    # 회수 뒤 3시간 안의 빈 구간(재고 0), 회수 뒤 2시간 안에 0이 된 경우만
    c.execute("""CREATE OR REPLACE TABLE emp AS
    SELECT pk.st, pk.m, pk.n, greatest(ser.ts, pk.m) e0, least(ser.ts_next, pk.m + INTERVAL 3 HOUR) e1, ser.dx_next
    FROM pk JOIN ser ON ser.st = pk.st AND ser.stock = 0 AND ser.ts < pk.m + INTERVAL 3 HOUR AND ser.ts_next > pk.m""")
    c.execute("""CREATE OR REPLACE TABLE over_ AS SELECT st, m FROM emp GROUP BY ALL HAVING min(e0) <= m + INTERVAL 2 HOUR""")
    res = c.sql("""
    WITH e AS (SELECT emp.* FROM emp JOIN over_ USING (st, m)),
    lost AS (SELECT e.st, e.m, sum(date_diff('second', e0, e1) / 3600.0 * coalesce(rate.per_h, 0)) exp_lost,
                    sum(date_diff('second', e0, e1)) / 60.0 empty_min
             FROM e LEFT JOIN rate ON rate.st = e.st AND rate.h = hour(e.e0) AND rate.we = (isodow(e.e0) >= 6) GROUP BY ALL),
    quick AS (SELECT o.st, o.m, count(*) n_quick FROM over_ o JOIN ser s ON s.st = o.st AND s.ts > o.m AND s.ts < o.m + INTERVAL 3 HOUR
              AND s.stock = 1 AND s.dx = 1 AND s.dx_next = -1 AND s.ts_next <= s.ts + INTERVAL 10 MINUTE
              AND EXISTS (SELECT 1 FROM ser p WHERE p.st = s.st AND p.ts_next = s.ts AND p.stock = 0) GROUP BY ALL)
    SELECT (SELECT count(*) FROM pk) pickups, count(*) over_cases, round(100 * count(*) / (SELECT count(*) FROM pk), 1) pct,
           round(median(empty_min)) med_empty_min, round(median(exp_lost), 1) med_exp_lost, round(sum(exp_lost)) sum_exp_lost,
           round(100 * avg((exp_lost >= 1)::INT), 1) pct_cases_lost_ge1,
           round(100 * avg((coalesce(n_quick, 0) >= 1)::INT), 1) pct_cases_quick_rerent, sum(coalesce(n_quick, 0)) n_quick_total
    FROM lost LEFT JOIN quick USING (st, m)""").df()
    print(f"\n== {A[:7]}")
    print(res.T.to_string())
    # 비교: 같은 달 낮 시간(07~21시) 모든 빈 구간에서 1시간당 '10분 안 재대여' 횟수 vs 과다 회수 뒤 빈 구간
    print(c.sql("""
    WITH z AS (SELECT st, ts, ts_next FROM ser WHERE stock = 0 AND hour(ts) BETWEEN 7 AND 20 AND ts_next IS NOT NULL),
    q AS (SELECT s.st, s.ts FROM ser s JOIN ser p ON p.st = s.st AND p.ts_next = s.ts AND p.stock = 0
          WHERE s.stock = 1 AND s.dx = 1 AND s.dx_next = -1 AND s.ts_next <= s.ts + INTERVAL 10 MINUTE AND hour(s.ts) BETWEEN 7 AND 20)
    SELECT round((SELECT count(*) FROM q) / (SELECT sum(date_diff('second', ts, ts_next)) / 3600.0 FROM z), 3) "모든 빈 구간: 빈 1시간당 즉시 재대여",
           round((SELECT sum(cnt) FROM (SELECT count(*) cnt FROM over_ o JOIN q ON q.st = o.st AND q.ts > o.m AND q.ts < o.m + INTERVAL 3 HOUR GROUP BY ALL))
                 / (SELECT sum(date_diff('second', e0, e1)) / 3600.0 FROM emp JOIN over_ USING (st, m)), 3) "과다 회수 뒤: 빈 1시간당 즉시 재대여"
    """).df().T.to_string())
