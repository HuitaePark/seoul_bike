"""재배치 역산 14단계: 대형 행사장 주변 대여소에서 이용이 평소보다 크게 튄 날 찾기 (팀 프로젝트 탐색용 임시 파일)

무엇을: 행사장 반경 700m 대여소의 날별 대여+반납을 같은 달·같은 요일 유형(평일/주말·공휴일) 중앙값과 비교해 배수를 구하고,
        배수가 큰 날을 행사 후보로 뽑는다. 그날 트럭 회수·배치(02_visits)도 평소와 비교한다. 행사 이름은 웹에서 따로 확인한다.
입력: 대여이력 2025(읽기만), out/visits_2025.parquet, station_master.csv, 공휴일(ops.duckdb 읽기 전용)
출력: out/event_days.csv, 화면
"""
from pathlib import Path

import duckdb

HERE = Path(__file__).parent
O = HERE / "out"
R = "/Users/junyeop/dareungi"
VENUES = {"광화문": (37.5720, 126.9769), "잠실종합운동장": (37.5150, 127.0730), "서울월드컵경기장": (37.5683, 126.8972),
          "올림픽공원": (37.5207, 127.1215), "고척스카이돔": (37.4982, 126.8671), "여의도한강공원": (37.5284, 126.9326)}
c = duckdb.connect()
c.execute("CREATE TABLE venue(v VARCHAR, lat DOUBLE, lon DOUBLE)")
c.executemany("INSERT INTO venue VALUES (?, ?, ?)", [(k, *v) for k, v in VENUES.items()])
c.execute(f"""CREATE TABLE vs AS SELECT v, st_no FROM venue, read_csv('{R}/data/interim/station_master.csv') s
WHERE s.st_no IS NOT NULL AND 2 * 6371000 * asin(sqrt(pow(sin(radians(s.lat - venue.lat) / 2), 2)
      + cos(radians(venue.lat)) * cos(radians(s.lat)) * pow(sin(radians(s.lon - venue.lon) / 2), 2))) <= 700""")
print(c.sql("SELECT v, count(*) n_st FROM vs GROUP BY 1"))
o = duckdb.connect(f"{R}/data/db/ops.duckdb", read_only=True)
c.register("h", o.sql("SELECT date FROM holidays WHERE year(date) = 2025").df())
o.close()
P = f"read_parquet('{R}/data/processed/rent/y=2025/*/*.parquet')"
c.execute(f"""CREATE TABLE ev AS SELECT vs.v, e.ts, e.x FROM (
  SELECT rent_st st, rent_dt ts, -1 x FROM {P} UNION ALL SELECT rtn_st, rtn_dt, 1 FROM {P} WHERE rtn_st IS NOT NULL) e JOIN vs ON vs.st_no = e.st""")
c.execute(f"""CREATE TABLE dd AS
WITH d AS (SELECT v, ts::DATE d, count(*) FILTER (WHERE x = -1) rent, count(*) FILTER (WHERE x = 1) rtn FROM ev GROUP BY ALL),
r AS (SELECT vs.v, (lo + (hi - lo) / 2)::DATE d, sum(n) FILTER (WHERE kind = 'pick') pick, sum(n) FILTER (WHERE kind = 'drop') drp
      FROM '{O}/visits_2025.parquet' JOIN vs ON vs.st_no = st GROUP BY ALL)
SELECT d.*, coalesce(pick, 0) pick, coalesce(drp, 0) drp, (isodow(d.d) >= 6 OR d.d IN (SELECT date::DATE FROM h)) is_off, month(d.d) mo
FROM d LEFT JOIN r USING (v, d)""")
c.execute("""CREATE TABLE rel AS
WITH b AS (SELECT v, mo, is_off, median(rent + rtn) b_use, median(pick) b_pick, median(drp) b_drp FROM dd GROUP BY ALL)
SELECT dd.*, (rent + rtn) / b_use x_use, b_pick, b_drp FROM dd JOIN b USING (v, mo, is_off)""")
c.execute(f"COPY (SELECT * FROM rel ORDER BY v, d) TO '{O}/event_days.csv'")
print(c.sql("""SELECT v, d, dayname(d) dow, round(x_use, 1) x_use, rent, rtn, pick, b_pick, drp, b_drp,
  row_number() OVER (PARTITION BY v ORDER BY x_use DESC) r FROM rel QUALIFY r <= 6 ORDER BY v, r""").df().to_string(index=False))
print("\n배수 2배 이상인 날 수(행사장별)")
print(c.sql("SELECT v, count(*) FILTER (WHERE x_use >= 2) days_2x, count(*) FILTER (WHERE x_use >= 1.5) days_1_5x FROM rel GROUP BY 1"))
