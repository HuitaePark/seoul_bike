"""재배치 역산 3단계: 대여소별 '이용 불균형'과 '트럭 회수·배치'를 같이 놓고 유형별로 본다. (팀 프로젝트 탐색용 임시 파일)

무엇을: 2025-04 ~ 10(성수기) 대여소별 대여·반납·재배치 반입·반출을 하루 평균으로 세고, 대여소 성격(이용 시간대 군집,
        상대 고도, 역 이름, 한강)별로 묶는다. 하루 단위로 '반납 − 대여'의 부호가 얼마나 일정한지도 본다.
주의: 자전거 수는 보존되므로 긴 기간의 (반납 − 대여) = (반출 − 반입, 정비 포함)은 거의 항등식이다.
      그래서 둘의 상관은 '발견'이 아니다. 정보는 ①어떤 성격의 대여소가 어느 쪽인지 ②그 역할이 날마다 일정한지에 있다.
입력: out/gaps_2025.parquet, 본 프로젝트 station_master.csv·station_cluster.csv(읽기만)
출력: out/station_roles.csv, 화면 출력
"""
from pathlib import Path

import duckdb

HERE = Path(__file__).parent
R = "/Users/junyeop/dareungi/data/interim"
c = duckdb.connect()
c.execute(f"""
CREATE TABLE g AS SELECT *, (moved AND NOT had_report AND gap_h < 72 AND dist_m BETWEEN 150 AND 10000) AS rebal
FROM '{HERE}/out/gaps_2025.parquet' WHERE t_to >= '2025-04-01' AND t_to < '2025-11-01'""")
D = 214  # 4 ~ 10월 날수
c.execute(f"""
CREATE TABLE st AS
WITH s AS (SELECT s.*, k.cluster_k5 AS cl,
             elev_m - median(elev_m) OVER (PARTITION BY district) AS rel_elev,
             (st_name LIKE '%역%' OR st_name LIKE '%출구%') AS near_sub
           FROM read_csv('{R}/station_master.csv') s LEFT JOIN read_csv('{R}/station_cluster.csv') k USING (st_no)),
rent AS (SELECT to_st st, count(*) / {D} rentals, sum(rebal::INT) / {D} reb_in, sum(moved::INT) / {D} mv_in FROM g GROUP BY 1),
rtn  AS (SELECT from_st st, count(*) / {D} rtns, sum(rebal::INT) / {D} reb_out, sum(moved::INT) / {D} mv_out FROM g GROUP BY 1)
SELECT s.st_no, s.st_name, s.district, s.hold_num, s.cl, s.rel_elev, s.near_sub, s.han_river,
       rentals, rtns, rtns - rentals AS ride_net, reb_in, reb_out, reb_in - reb_out AS reb_net, mv_in - mv_out AS mv_net
FROM s JOIN rent ON rent.st = s.st_no JOIN rtn ON rtn.st = s.st_no""")
c.execute(f"COPY st TO '{HERE}/out/station_roles.csv'")

print("보존 점검: ride_net vs -(mv_net) 상관",
      c.sql("SELECT round(corr(ride_net, -mv_net), 3), round(corr(ride_net, -reb_net), 3) FROM st").fetchone())

# 역할: 하루 순반입(재배치) 기준. 2대/일 이상 반입 = 배치처(sink), 반출 = 회수처(source)
role = """CASE WHEN reb_net >= 2 THEN '배치처(반입)' WHEN reb_net <= -2 THEN '회수처(반출)' ELSE '균형' END"""
print(c.sql(f"SELECT {role} AS st_role, count(*) n_st, round(sum(reb_in)) in_day, round(sum(reb_out)) out_day FROM st GROUP BY 1 ORDER BY 1"))

for name, key in [("이용 시간대 군집(k=5)", "cl"),
                  ("자치구 안 상대 고도", "CASE WHEN rel_elev < -10 THEN 'a 낮음(<-10m)' WHEN rel_elev < 10 THEN 'b 중간' WHEN rel_elev < 30 THEN 'c 높음(10~30m)' ELSE 'd 매우 높음(30m+)' END"),
                  ("역 이름 포함", "near_sub"), ("한강", "han_river")]:
    print("\n==", name)
    print(c.sql(f"""SELECT {key} grp, count(*) n_st, round(avg(rentals), 1) rent_day,
      round(sum(ride_net) / count(*), 2) ride_net_per_st,
      round(100 * sum(reb_in) / sum(rentals), 1) pct_rent_supplied_by_truck,
      round(100 * sum(reb_out) / sum(rtns), 1) pct_return_taken_by_truck,
      round(100 * avg((({role}) = '배치처(반입)')::INT), 0) pct_sink, round(100 * avg((({role}) = '회수처(반출)')::INT), 0) pct_source
      FROM st GROUP BY 1 ORDER BY 1""").df().to_string(index=False))

# 날마다 역할이 같은가: 대여소×날 (반납 − 대여) 부호가 그 대여소의 기간 평균 부호와 같은 날 비율
print("\n== 하루 단위 역할 일관성(이용량 하루 20건 이상 대여소)")
print(c.sql("""
WITH d AS (SELECT st, dt, sum(x) net FROM (
    SELECT from_st st, t_from::DATE dt, 1 x FROM g UNION ALL SELECT to_st, t_to::DATE, -1 FROM g) GROUP BY ALL),
m AS (SELECT st, avg(net) mean_net, count(*) ndays FROM d GROUP BY 1)
SELECT CASE WHEN abs(mean_net) >= 10 THEN 'a |평균|>=10' WHEN abs(mean_net) >= 3 THEN 'b 3~10' ELSE 'c <3' END grp,
       count(DISTINCT st) n_st, round(100 * avg((sign(net) = sign(mean_net))::INT), 0) pct_days_same_sign
FROM d JOIN m USING (st) WHERE st IN (SELECT st_no FROM st WHERE rentals >= 20) AND ndays > 150 GROUP BY 1 ORDER BY 1"""))
