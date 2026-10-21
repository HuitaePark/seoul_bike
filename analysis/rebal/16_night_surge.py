"""재배치 역산 16단계: 공연장 주변 대여소의 '끝나고 몰려 빌려 가는 밤' 찾기 (팀 프로젝트 탐색용 임시 파일)

무엇을: 공연장 반경 500m 대여소의 20~23시 시간별 대여를 서울 전체 대여 비중으로 바꾸고, 같은 시각·같은 달·같은 요일 유형
        중앙값의 몇 배인지 본다. 3배 이상 & 40건 이상인 밤을 행사 후보로 뽑는다(공연 이름은 웹에서 맞춘다).
입력: 대여이력 2025(읽기만), station_master.csv / 출력: out/night_surge.csv, 화면
"""
import os
from pathlib import Path

import duckdb

HERE = Path(__file__).parent
O = HERE / "out"
DAR = os.environ.get("DAREUNGI", "/Users/junyeop/dareungi")  # 따릉이 고장신고 프로젝트(대여이력 원본) 위치. README 참고
R = DAR
VENUES = {"KSPO DOME(올림픽공원)": (37.5203, 127.1268), "고척스카이돔": (37.4982, 126.8671), "서울월드컵경기장": (37.5683, 126.8972),
          "잠실종합운동장": (37.5150, 127.0730), "장충체육관": (37.5583, 127.0068)}
c = duckdb.connect()
c.execute("CREATE TABLE venue(v VARCHAR, lat DOUBLE, lon DOUBLE)")
c.executemany("INSERT INTO venue VALUES (?, ?, ?)", [(k, *v) for k, v in VENUES.items()])
c.execute(f"""CREATE TABLE vs AS SELECT v, st_no FROM venue, read_csv('{R}/data/interim/station_master.csv') s WHERE s.st_no IS NOT NULL AND
 2 * 6371000 * asin(sqrt(pow(sin(radians(s.lat - venue.lat) / 2), 2) + cos(radians(venue.lat)) * cos(radians(s.lat)) * pow(sin(radians(s.lon - venue.lon) / 2), 2))) <= 500""")
print(c.sql("SELECT v, count(*) n_st FROM vs GROUP BY 1"))
P = f"read_parquet('{R}/data/processed/rent/y=2025/*/*.parquet')"
c.execute(f"""CREATE TABLE hv AS SELECT vs.v, date_trunc('hour', rent_dt) hr, count(*) rent FROM {P} t JOIN vs ON vs.st_no = t.rent_st GROUP BY ALL""")
c.execute(f"CREATE TABLE ch AS SELECT date_trunc('hour', rent_dt) hr, count(*) city FROM {P} GROUP BY 1")
c.execute("""CREATE TABLE hx AS
WITH x AS (SELECT hv.*, hour(hr) h, month(hr) mo, isodow(hr) >= 6 we, rent / city s FROM hv JOIN ch USING (hr) WHERE hour(hr) BETWEEN 20 AND 23),
b AS (SELECT v, h, mo, we, median(s) bs, median(rent) brent FROM x GROUP BY ALL)
SELECT x.v, x.hr, x.rent, b.brent, s / bs xr FROM x JOIN b USING (v, h, mo, we)""")
c.execute("""CREATE TABLE nights AS SELECT v, hr::DATE d, dayname(hr) dow, arg_max(hour(hr), xr) peak_h, max(rent) peak_rent, round(max(xr), 1) x
FROM hx WHERE xr >= 3 AND rent >= 40 GROUP BY v, hr::DATE, dayname(hr) ORDER BY v, d""")
c.execute(f"COPY nights TO '{O}/night_surge.csv'")
print(c.sql("SELECT v, count(*) nights FROM nights GROUP BY 1"))
for v in VENUES:
    print(f"\n== {v}")
    print(c.sql(f"SELECT d, dow, peak_h, peak_rent, x FROM nights WHERE v = '{v}' ORDER BY d").df().to_string(index=False))
