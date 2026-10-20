"""재배치 역산 4단계: 대여이력만으로 대여소 재고를 복원하고, 트럭이 '재고가 몇 대일 때' 오는지 본다. (팀 프로젝트 탐색용 임시 파일)

무엇을: 자전거 한 대는 반납한 대여소에 다음 대여까지 서 있다고 본다(같은 대여소 구간).
        재배치 후보는 회수 방문 창 중간에 원래 대여소를 떠나 배치 방문 창 중간에 새 대여소에 생긴다고 본다.
        정비·기타 이동(고장신고·72시간 이상 등)은 공백 동안 어디에도 세지 않는다 → 재고는 약간 낮게 잡힌다(하한 쪽).
        대여소×시각 재고 = 그 시각을 덮는 구간 수. 2025-05 한 달.
왜: PRD 시뮬레이터는 '초기 재고 + 대여 재생'이 필요하고, 실시간 재고 이력은 이 저장소에 없다.
    또 트럭이 오는 직전 재고 분포가 '언제 재배치하나(트리거)'의 근거가 된다.
입력: out/gaps_2025.parquet, out/bike_pick·bike_drop.parquet, station_master.csv / 출력: 화면
"""
from pathlib import Path

import duckdb

HERE = Path(__file__).parent
O = HERE / "out"
c = duckdb.connect()
c.execute(f"SET memory_limit='8GB'; SET temp_directory='{HERE / 'tmp'}'")
A, B = "2025-05-01", "2025-06-01"
c.execute(f"""
CREATE TABLE iv AS
WITH g AS (SELECT * FROM '{O}/gaps_2025.parquet' WHERE t_to >= '{A}' AND t_from < '{B}'),
p AS (SELECT bike_id, t_to, lo + (hi - lo) / 2 AS m FROM '{O}/bike_pick.parquet'),
d AS (SELECT bike_id, t_to, lo + (hi - lo) / 2 AS m FROM '{O}/bike_drop.parquet')
SELECT from_st AS st, t_from AS s, t_to AS e FROM g WHERE NOT moved
UNION ALL SELECT g.from_st, g.t_from, p.m FROM g JOIN p USING (bike_id, t_to)
UNION ALL SELECT g.to_st, d.m, g.t_to FROM g JOIN d USING (bike_id, t_to)""")

# 재고 시계열: 구간 시작 +1, 끝 −1 누적합. 같은 시각이면 −1을 먼저(대여가 반납보다 먼저 처리되게)
c.execute("""
CREATE TABLE ser AS
SELECT st, ts, sum(dx) OVER (PARTITION BY st ORDER BY ts, dx ROWS UNBOUNDED PRECEDING) AS stock,
       lead(ts) OVER (PARTITION BY st ORDER BY ts, dx) AS ts_next
FROM (SELECT st, s AS ts, 1 AS dx FROM iv UNION ALL SELECT st, e, -1 FROM iv)""")
c.execute(f"CREATE TABLE s AS SELECT * FROM read_csv('/Users/junyeop/dareungi/data/interim/station_master.csv')")

print("점검: 새벽 4시 전체 재고(매일) 중앙값 =",
      c.sql(f"""SELECT median(tot) FROM (SELECT d, sum(stock) tot FROM (
      SELECT DISTINCT ON (st, ts::DATE) st, ts::DATE d, stock FROM ser
      WHERE hour(ts) < 4 AND ts >= '{A}' AND ts < '{B}' ORDER BY st, ts::DATE, ts DESC) GROUP BY d)""").fetchone()[0])

# 낮(07~22시) 시간 가중: 재고 0인 시간 비율, 거치대 수 초과 비율
c.execute(f"""CREATE TABLE occ AS
SELECT ser.st, sum(date_diff('second', ts, ts_next)) FILTER (WHERE stock = 0) / sum(date_diff('second', ts, ts_next)) AS empty_share,
       sum(date_diff('second', ts, ts_next)) FILTER (WHERE stock > hold_num) / sum(date_diff('second', ts, ts_next)) AS over_share
FROM ser JOIN s ON s.st_no = ser.st
WHERE ts >= '{A}' AND ts_next < '{B}' AND hour(ts) BETWEEN 7 AND 21 GROUP BY 1""")
print(c.sql("""SELECT round(100 * avg(empty_share), 1) avg_pct_time_empty,
   round(100 * quantile_cont(empty_share, 0.9), 1) p90_pct_time_empty,
   count(*) FILTER (WHERE empty_share > 0.3) n_st_empty_30pct,
   round(100 * avg(over_share), 1) avg_pct_time_over_docks, count(*) n_st FROM occ"""))

# 배치·회수 방문 직전 재고(방문 창 시작 시각 기준) vs 같은 대여소 낮 시간 평소 재고
roles = f"read_csv('{O}/station_roles.csv')"
print(c.sql(f"""
WITH v AS (SELECT kind, st, lo, n FROM '{O}/visits_2025.parquet' WHERE lo >= '{A}' AND lo < '{B}' AND n >= 5),
at_v AS (SELECT v.kind, v.n, ser.stock, s.hold_num FROM v ASOF JOIN ser ON v.st = ser.st AND v.lo >= ser.ts JOIN s ON s.st_no = v.st)
SELECT kind, count(*) visits, quantile_cont(stock, [.25, .5, .75]) stock_before_q,
       round(100 * avg((stock = 0)::INT)) pct_empty_before, round(100 * avg((stock >= hold_num)::INT)) pct_full_before,
       median(hold_num) med_docks, median(n) med_moved
FROM at_v GROUP BY 1"""))
print(c.sql(f"""
SELECT r.st_role, count(*) n_st, round(100 * avg(empty_share), 1) pct_time_empty, round(100 * avg(over_share), 1) pct_time_over_docks
FROM occ JOIN (SELECT st_no, CASE WHEN reb_net >= 2 THEN '배치처' WHEN reb_net <= -2 THEN '회수처' ELSE '균형' END st_role FROM {roles}) r
ON r.st_no = occ.st GROUP BY 1 ORDER BY 1"""))
