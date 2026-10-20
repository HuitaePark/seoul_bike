# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: 대여소 마스터(번호 ↔ ST-ID 대응, 위치, 거치대, 고도, 역산 구역·역할)를 만들고 실시간 재고·대여이력과 맞는지 확인한다.

"""대여소 마스터와 ID 대응표 (M1 데이터 명세, 4 백엔드 ref.stations 시드의 원본).

왜: 대여이력은 5자리 대여소번호(st_no), 실시간 재고(bikeList)는 ST-ID(st_id)를 쓴다. 두 체계를 한 표로 묶어야
    재고 검증, 시뮬레이터, 백엔드 시드가 같은 대여소를 가리킨다.
입력: data/raw/stations/<가장 최근 날짜>/ (collect/stations_api.py가 받은 대여소 정보 API 원본),
      data/realtime/bikelist/ 가장 최근 스냅샷(운영 여부, 좌표 보충),
      고도: dareungi/data/interim/station_master.csv의 기존 값 + 새 대여소는 OpenTopoData(SRTM 30m)로 받아
            data/raw/stations/elev_opentopodata.csv에 쌓는다,
      analysis/rebal/out/territory.csv (역산 활동 구역), station_roles.csv (2025-04~10 하루 평균 이용·재배치),
      dareungi 2025 대여이력 Parquet(확인용)
출력: analysis/out/stations.csv, 화면에 확인표
실행: /Users/junyeop/dareungi/.venv/bin/python analysis/stations.py
"""
import csv
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DAR = Path("/Users/junyeop/dareungi")
REB = ROOT / "analysis/rebal/out"
OUT = ROOT / "analysis/out/stations.csv"
SNAP = sorted((ROOT / "data/realtime/bikelist").glob("*/*.csv.gz"))[-1]
RAW = sorted((ROOT / "data/raw/stations").glob("*/tbCycleStationInfo.json.gz"))[-1].parent
ELEV = ROOT / "data/raw/stations/elev_opentopodata.csv"
FIXED = 10  # PRD 7.1 초기값: 하루 평균 |반납 − 대여|가 이 이상이면 고정 회수·고정 배치
NORTH = ("종로구", "중구", "용산구", "성동구", "광진구", "동대문구", "중랑구", "성북구", "강북구", "도봉구", "노원구",
         "은평구", "서대문구", "마포구")  # 역산 09_territory.py와 같은 한강 기준



def fetch_elev(pts):
    """OpenTopoData SRTM 30m 고도. 요청당 100지점, 초당 1회 제한. pts: [(st_no, lat, lon)]"""
    out = []
    for i in range(0, len(pts), 100):
        chunk = pts[i:i + 100]
        q = urllib.parse.urlencode({"locations": "|".join(f"{lat},{lon}" for _, lat, lon in chunk)})
        with urllib.request.urlopen(f"https://api.opentopodata.org/v1/srtm30m?{q}", timeout=60) as r:
            res = json.load(r)["results"]
        out += [(no, lat, lon, x["elevation"]) for (no, lat, lon), x in zip(chunk, res)]
        time.sleep(1.1)
    return out


c = duckdb.connect()
c.execute(f"CREATE TABLE rt AS SELECT * FROM read_csv('{SNAP}', all_varchar = true)")
# 좌표는 대여소 정보 → 대여소 마스터 API → 실시간 재고 순으로 채운다(0은 빈 값으로 본다)
c.execute(f"""
CREATE TABLE m AS
SELECT i.RENT_NO::INT AS st_no, i.RENT_ID AS st_id, i.RENT_NM AS st_name, i.STA_LOC AS district, i.STA_ADD1 AS addr,
       coalesce(nullif(TRY_CAST(i.STA_LAT AS DOUBLE), 0), nullif(TRY_CAST(b.LAT AS DOUBLE), 0),
                nullif(TRY_CAST(rt.stationLatitude AS DOUBLE), 0)) AS lat,
       coalesce(nullif(TRY_CAST(i.STA_LONG AS DOUBLE), 0), nullif(TRY_CAST(b.LOT AS DOUBLE), 0),
                nullif(TRY_CAST(rt.stationLongitude AS DOUBLE), 0)) AS lon,
       TRY_CAST(i.HOLD_NUM AS INT) AS hold_num
FROM read_json('{RAW}/tbCycleStationInfo.json.gz', format = 'array') i
LEFT JOIN read_json('{RAW}/bikeStationMaster.json.gz', format = 'array') b ON b.RNTLS_ID = i.RENT_ID
LEFT JOIN rt ON rt.stationId = i.RENT_ID""")
# 고도: 기존 값이 있으면 쓰고, 없는 새 대여소만 받아 캐시에 쌓는다
c.execute(f"CREATE TABLE el AS SELECT st_no::INT AS st_no, elev_m FROM read_csv('{DAR}/data/interim/station_master.csv') WHERE st_no IS NOT NULL AND elev_m IS NOT NULL")
if ELEV.exists():
    c.execute(f"INSERT INTO el SELECT st_no, elev_m FROM read_csv('{ELEV}')")
need = c.execute("SELECT st_no, lat, lon FROM m WHERE lat IS NOT NULL AND st_no NOT IN (SELECT st_no FROM el)").fetchall()
if need:
    new = fetch_elev(need)
    first = not ELEV.exists()
    with open(ELEV, "a", newline="") as f:
        w = csv.writer(f)
        if first:
            w.writerow(["st_no", "lat", "lon", "elev_m"])
        w.writerows(new)
    c.executemany("INSERT INTO el VALUES (?, ?)", [(no, e) for no, _, _, e in new])
    print(f"새 대여소 고도 {len(new)}곳을 OpenTopoData에서 받음")
c.execute("CREATE TABLE m2 AS SELECT m.*, el.elev_m FROM m LEFT JOIN (SELECT st_no, any_value(elev_m) AS elev_m FROM el GROUP BY 1) el USING (st_no)")

c.execute(f"""
CREATE TABLE st AS
SELECT m.st_no, m.st_id, m.st_name, m.district, m.addr,
       CASE WHEN m.district IN {NORTH} THEN '강북' WHEN m.district LIKE '%구' THEN '강남' END AS region,
       t.terr, CASE WHEN t.terr IS NOT NULL THEN 'rebal' END AS terr_src,
       m.lat, m.lon, m.hold_num, m.elev_m,
       round(m.elev_m - median(m.elev_m) OVER (PARTITION BY m.district), 1) rel_elev,
       round(r.rentals, 1) rentals_d, round(r.rtns, 1) returns_d, round(r.ride_net, 1) ride_net_d,
       round(r.reb_in, 1) reb_in_d, round(r.reb_out, 1) reb_out_d,
       CASE WHEN r.ride_net >= {FIXED} THEN '고정 회수' WHEN r.ride_net <= -{FIXED} THEN '고정 배치'
            WHEN r.rentals IS NULL THEN NULL ELSE '변동' END AS role
FROM m2 m
LEFT JOIN read_csv('{REB}/territory.csv') t ON t.st_no = m.st_no
LEFT JOIN read_csv('{REB}/station_roles.csv') r ON r.st_no = m.st_no
ORDER BY st_no""")
# 2025 재배치 기록이 없어 구역이 빈 대여소(대부분 지금 운영하지 않는 곳, 일부 2026년 신설)는 같은 강북·강남 안에서 가장 가까운 대여소의 구역에 붙인다
c.execute("""
UPDATE st SET terr = n.terr, terr_src = 'nearest' FROM (
  SELECT a.st_no, arg_min(b.terr, (a.lat - b.lat) ^ 2 + ((a.lon - b.lon) * cos(radians(a.lat))) ^ 2) AS terr
  FROM st a JOIN st b ON a.region = b.region AND b.terr_src = 'rebal'
  WHERE a.terr IS NULL GROUP BY 1) n
WHERE st.st_no = n.st_no AND n.terr IS NOT NULL""")  # 좌표가 없으면 붙이지 않는다
# 대여소 정보 API에는 폐쇄·이전한 대여소도 남아 있다. 가장 최근 수집일의 실시간 재고에 한 번이라도 나온 곳을 운영 중으로 본다
c.execute(f"""ALTER TABLE st ADD COLUMN active BOOLEAN;
UPDATE st SET active = st_id IN (SELECT DISTINCT stationId FROM read_csv('{SNAP.parent}/*.csv.gz', all_varchar = true))""")

n, n_no, n_id = c.execute("SELECT count(*), count(DISTINCT st_no), count(DISTINCT st_id) FROM st").fetchone()
assert n == n_no == n_id, f"번호·ID가 1:1이 아님: 행 {n}, 번호 {n_no}, ID {n_id}"

print(f"대여소 마스터 {n}곳 (번호·ST-ID 1:1)")
print(f"\n== 실시간 재고 스냅샷 {SNAP.parent.name} {SNAP.stem} 과 대조")
print(c.sql("""
SELECT count(*) "실시간 대여소", count(st.st_id) "마스터에 있음",
       count(*) FILTER (WHERE TRY_CAST(split_part(rt.stationName, '.', 1) AS INT) = st.st_no) "이름 앞 번호 = st_no",
       count(*) FILTER (WHERE st.hold_num IS NOT NULL AND rt.rackTotCnt::INT <> st.hold_num) "거치대 수 다름"
FROM rt LEFT JOIN st ON st.st_id = rt.stationId""").df().to_string(index=False))
print(c.sql("SELECT rt.stationId, rt.stationName FROM rt ANTI JOIN st ON st.st_id = rt.stationId LIMIT 5").df().to_string(index=False))

print("\n== 2025 대여이력의 대여소번호가 마스터에 있는 비율 (대여 건 기준)")
rent = (DAR / "data/processed/rent/y=2025/*/*.parquet").as_posix()
print(c.sql(f"""
SELECT round(100 * avg((s1.st_no IS NOT NULL)::INT), 2) "대여 대여소 %",
       round(100 * avg((s2.st_no IS NOT NULL)::INT) FILTER (WHERE r.rtn_st IS NOT NULL), 2) "반납 대여소 %",
       round(100 * avg((r.rtn_st IS NULL)::INT), 2) "반납 대여소 빈 값 %"
FROM read_parquet('{rent}') r LEFT JOIN st s1 ON s1.st_no = r.rent_st LEFT JOIN st s2 ON s2.st_no = r.rtn_st""").df().to_string(index=False))

print("\n== 구역 출처 (rebal: 역산 군집, nearest: 가장 가까운 대여소의 구역)")
print(c.sql("""SELECT coalesce(terr_src, '없음') AS terr_src, count(*) AS n, count(*) FILTER (WHERE active) AS 운영중
               FROM st GROUP BY 1 ORDER BY 2 DESC""").df().to_string(index=False))

print("\n== 역할 (PRD 7.1 초기값: 하루 평균 |반납 − 대여| ≥ 10)")
print(c.sql("SELECT coalesce(role, '2025 이용 없음') AS role, count(*) n FROM st GROUP BY 1 ORDER BY 2 DESC").df().to_string(index=False))

OUT.parent.mkdir(exist_ok=True)
c.execute(f"COPY st TO '{OUT}' (HEADER)")
print(f"\n저장: {OUT.relative_to(ROOT)}")
