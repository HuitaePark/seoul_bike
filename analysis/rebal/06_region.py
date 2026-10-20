"""재배치 역산 6단계: 강북·강남 두 권역으로 나눴을 때 (팀 프로젝트 탐색용 임시 파일)

무엇을: ① 트럭 재배치 중 한강을 건너는 비율·방향·구 쌍 ② 이용자가 강을 건너며 옮기는 자전거(날·시각·요일별 순대수)
        ③ 권역 안에서 남는 자전거(반납 > 대여)로 모자란 곳(대여 > 반납)을 채울 수 있는지(근무조 구간별)
권역: 자치구 기준. 한강공원 대여소는 속한 구를 따른다.
입력: out/gaps_2025.parquet(재배치 후보·이동), 본 프로젝트 대여이력 2025·station_master.csv(읽기만)
출력: 화면
"""
from pathlib import Path

import duckdb

HERE = Path(__file__).parent
O = HERE / "out"
R = "/Users/junyeop/dareungi"
NORTH = ("종로구", "중구", "용산구", "성동구", "광진구", "동대문구", "중랑구", "성북구", "강북구", "도봉구", "노원구",
         "은평구", "서대문구", "마포구")
c = duckdb.connect()
c.execute(f"SET memory_limit='8GB'; SET temp_directory='{HERE / 'tmp'}'")
c.execute(f"""CREATE TABLE s AS SELECT st_no, district, CASE WHEN district IN {NORTH} THEN '강북' ELSE '강남' END rg, hold_num
              FROM read_csv('{R}/data/interim/station_master.csv') WHERE district LIKE '%구' AND district <> '분당구'""")
assert c.sql("SELECT count(DISTINCT district) FROM s").fetchone()[0] == 25
print(c.sql("SELECT rg, count(DISTINCT district) gu, count(*) n_st, sum(hold_num) docks FROM s GROUP BY 1"))

# ---------- 1. 트럭 재배치가 강을 건너는가
c.execute(f"""CREATE TABLE mv AS
SELECT g.*, a.rg rf, b.rg rt, a.district df, b.district dt_,
       (NOT had_report AND gap_h < 72 AND dist_m BETWEEN 150 AND 10000) rebal
FROM '{O}/gaps_2025.parquet' g JOIN s a ON a.st_no = g.from_st JOIN s b ON b.st_no = g.to_st WHERE moved""")
print("\n== 1. 강을 건너는 이동 비율")
print(c.sql("""SELECT CASE WHEN rebal THEN '재배치 후보' ELSE '그 외 이동(정비·장기 등)' END k, count(*) n,
  round(100 * avg((rf <> rt)::INT), 1) pct_cross,
  count(*) FILTER (WHERE rf = '강북' AND rt = '강남') n_to_s, count(*) FILTER (WHERE rf = '강남' AND rt = '강북') s_to_n,
  round(median(dist_m) FILTER (WHERE rf <> rt)) med_dist_cross, round(median(dist_m) FILTER (WHERE rf = rt)) med_dist_within
  FROM mv GROUP BY 1"""))
print(c.sql("""SELECT month(t_to) mo, round(100 * avg((rf <> rt)::INT), 1) pct_cross FROM mv WHERE rebal GROUP BY 1 ORDER BY 1""").df().T.to_string())
print("강을 건너는 재배치 후보: 구 쌍 상위")
print(c.sql("""SELECT df || ' → ' || dt_ pair, count(*) n, round(100 * count(*) / sum(count(*)) OVER (), 1) pct, round(median(dist_m)) med_m
  FROM mv WHERE rebal AND rf <> rt GROUP BY 1 ORDER BY 2 DESC LIMIT 10""").df().to_string(index=False))

# ---------- 2. 이용자가 강을 건너는가
c.execute(f"""CREATE TABLE ride AS
SELECT t.rent_dt, t.rtn_dt, a.rg rf, b.rg rt, a.district df, b.district dt_
FROM read_parquet('{R}/data/processed/rent/y=2025/*/*.parquet') t JOIN s a ON a.st_no = t.rent_st JOIN s b ON b.st_no = t.rtn_st
WHERE t.rtn_dt IS NOT NULL""")
o = duckdb.connect(f"{R}/data/db/ops.duckdb", read_only=True)
c.register("h", o.sql("SELECT date FROM holidays WHERE year(date) = 2025").df())
o.close()
c.execute("CREATE TABLE hol AS SELECT date::DATE d FROM h")
c.execute("""CREATE TABLE rday AS SELECT rent_dt::DATE d, (isodow(rent_dt) >= 6 OR rent_dt::DATE IN (SELECT d FROM hol)) is_off,
  count(*) n_all, count(*) FILTER (WHERE rf = '강북' AND rt = '강남') n2s, count(*) FILTER (WHERE rf = '강남' AND rt = '강북') s2n,
  count(*) FILTER (WHERE rf = '강북') rent_n, count(*) FILTER (WHERE rf = '강남') rent_s
  FROM ride GROUP BY ALL""")
print("\n== 2. 이용자의 강 건너기(하루 평균)")
print(c.sql("""SELECT CASE WHEN month(d) BETWEEN 4 AND 10 THEN '4~10월' ELSE '11~3월' END season, CASE WHEN is_off THEN '주말·공휴일' ELSE '평일' END dd,
  count(*) n_days, round(avg(n2s)) n2s, round(avg(s2n)) s2n, round(avg(n2s - s2n)) net_n2s, round(100 * avg((n2s + s2n) / n_all), 1) pct_cross_rides,
  round(100 * avg(((n2s - s2n) > 0)::INT)) pct_days_n2s_positive, round(avg(rent_n)) rent_n, round(avg(rent_s)) rent_s
  FROM rday GROUP BY ALL ORDER BY 1 DESC, 2"""))
print("연간 순이동(강북→강남, 이용자):", c.sql("SELECT sum(n2s - s2n) FROM rday").fetchone())
print("연간 순이동(강북→강남, 재배치 후보 트럭):", c.sql("SELECT sum(CASE WHEN rf='강북' AND rt='강남' THEN 1 WHEN rf='강남' AND rt='강북' THEN -1 ELSE 0 END) FROM mv WHERE rebal").fetchone(),
      " / 그 외 이동:", c.sql("SELECT sum(CASE WHEN rf='강북' AND rt='강남' THEN 1 WHEN rf='강남' AND rt='강북' THEN -1 ELSE 0 END) FROM mv WHERE NOT rebal").fetchone())
print("평일 시각별 강 건너기(대여 시각, 4~10월 하루 평균)")
print(c.sql("""SELECT hour(rent_dt) h, round(count(*) FILTER (WHERE rf = '강북' AND rt = '강남') / 144.0) n2s,
  round(count(*) FILTER (WHERE rf = '강남' AND rt = '강북') / 144.0) s2n FROM ride
  WHERE month(rent_dt) BETWEEN 4 AND 10 AND isodow(rent_dt) <= 5 AND rent_dt::DATE NOT IN (SELECT d FROM hol) AND rf <> rt GROUP BY 1 ORDER BY 1""").df().T.to_string())
print("이용자 강 건너기: 구 쌍 상위(양방향 합)")
print(c.sql("""SELECT least(df, dt_) || ' ↔ ' || greatest(df, dt_) pair, count(*) / 365 per_day,
  round(100 * count(*) / sum(count(*)) OVER (), 1) pct FROM ride WHERE rf <> rt GROUP BY 1 ORDER BY 2 DESC LIMIT 8""").df().to_string(index=False))

# ---------- 3. 권역 안에서 자급할 수 있는가: 근무조 구간별 대여소 순반납(반납 − 대여)
# 구간: 아침(06~13시, 오전 근무조 07~10시가 처리) / 낮(13~19시, 오후 13~16시) / 저녁·밤(19~다음날 06시, 저녁 19~20시)
c.execute("""CREATE TABLE blk AS
WITH e AS (SELECT rtn_st st, rtn_dt ts, 1 x FROM read_parquet('""" + R + """/data/processed/rent/y=2025/*/*.parquet') WHERE rtn_dt IS NOT NULL
           UNION ALL SELECT rent_st, rent_dt, -1 FROM read_parquet('""" + R + """/data/processed/rent/y=2025/*/*.parquet')),
e2 AS (SELECT st, x, CASE WHEN hour(ts) BETWEEN 6 AND 12 THEN 'a 아침 06~13' WHEN hour(ts) BETWEEN 13 AND 18 THEN 'b 낮 13~19' ELSE 'c 저녁·밤 19~06' END p,
           (ts - INTERVAL 6 HOUR)::DATE d FROM e WHERE month(ts) BETWEEN 4 AND 10)
SELECT s.rg, d, p, st, sum(x) net FROM e2 JOIN s ON s.st_no = e2.st GROUP BY ALL""")
print("\n== 3. 권역 자급: 구간마다 남는 자전거(Σ순반납>0) ÷ 모자란 자전거(Σ순반납<0), 4~10월")
print(c.sql("""WITH r AS (SELECT rg, d, p, sum(greatest(net, 0)) sup, sum(greatest(-net, 0)) dem FROM blk GROUP BY ALL)
SELECT rg, p, count(*) n_days, round(avg(sup)) supply, round(avg(dem)) demand, round(avg(sup - dem)) net,
  round(median(sup / dem), 2) med_ratio, round(100 * avg((sup < dem)::INT)) pct_days_short
FROM r GROUP BY ALL ORDER BY 1, 2"""))
print("하루 단위(대여소별 하루 순반납)")
print(c.sql("""WITH st AS (SELECT rg, d, st, sum(net) net FROM blk GROUP BY ALL),
r AS (SELECT rg, d, sum(greatest(net, 0)) sup, sum(greatest(-net, 0)) dem FROM st GROUP BY ALL)
SELECT rg, round(avg(sup)) supply, round(avg(dem)) demand, round(median(sup / dem), 2) med_ratio, round(100 * avg((sup < dem)::INT)) pct_days_short FROM r GROUP BY 1"""))
