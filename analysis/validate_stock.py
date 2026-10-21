# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: 실시간 재고와 같은 시간의 대여이력으로 트럭 재배치를 직접 관측해, 대여이력 역산의 추정과 비교한다.

"""역산 재고 검증 (M2, PRD 1.5·12).

① 거치대 초과: 재고가 거치대 수를 넘는 일이 실제로 있는가. 역산은 회수 직전 재고 중앙값을 31대(거치대 중앙값 10개)로 추정했다.
② 트럭 사건: 대여소별 연속 스냅샷 (t0, t1] 사이 '재고 변화 − (반납 − 대여)'가 트럭이 넣거나 뺀 대수다.
   절댓값이 EVENT 이상인 구간을 트럭 사건으로 보고, 같은 대여소에서 MERGE_MIN 안에 이어지면 하나로 합친다.
   방문당 대수, 시간대, 방문 직전 재고를 역산 추정(배치 직전 2대·30%가 0대, 회수 직전 31대, 13~15대, 근무조)과 비교한다.
주의: 대여이력 API는 반납이 끝난 대여만 준다. 최근 구간은 대여가 덜 잡혀 회수처럼 보이므로, 실행 시각보다 MATURE_H 이상 지난 구간만 쓴다.
      같은 대여소에 1분 이하로 반납한 대여는 재고를 바꾸지 않으므로 뺀다.
입력: data/realtime/bikelist/ (collect/bikelist.py), 서울 API tbCycleRentData (시간대별, data/raw/rent_hourly/<날짜>/<시>.json.gz에 캐시),
      analysis/out/stations.csv (역할)
출력: 화면, data/derived/truck_events.csv
실행: .venv/bin/python analysis/validate_stock.py
"""
import datetime as dt
import gzip
import json
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis/collect"))
from bikelist import KST, api_key, fetch_all, get_page  # noqa: E402

CACHE = ROOT / "data/raw/rent_hourly"
EVENT = 3  # 트럭 사건으로 볼 최소 대수 (초기값. 5분 사이 대여·반납 시각 어긋남은 대부분 1~2대)
MERGE_MIN = 15  # 같은 대여소에서 이 안에 이어진 같은 방향 사건은 한 방문으로 본다
MATURE_H = 2  # 이보다 최근 구간은 아직 반납 안 된 대여가 많아 쓰지 않는다


def rent_hour(key: str, h: dt.datetime) -> list[dict]:
    """h시에 대여된 건(반납까지 끝난 것)을 받는다. 하루 넘게 지난 시간대는 캐시를 쓰고, 그보다 최근이면 다시 받는다."""
    f = CACHE / f"{h:%Y-%m-%d}" / f"{h:%H}.json.gz"
    if f.exists() and dt.datetime.fromtimestamp(f.stat().st_mtime) - h >= dt.timedelta(hours=26):
        return json.loads(gzip.open(f, "rt", encoding="utf-8").read())
    rows = fetch_all(lambda a, b: get_page(key, a, b, "tbCycleRentData", (f"{h:%Y-%m-%d}", f"{h:%H}")))
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as out:
        json.dump(rows, out, ensure_ascii=False)
    tmp.replace(f)
    return rows


def show(c, title, sql):
    print(f"\n== {title}")
    print(c.sql(sql).df().to_string(index=False))


def main() -> None:
    now = dt.datetime.now(KST).replace(tzinfo=None)
    cutoff = now - dt.timedelta(hours=MATURE_H)
    c = duckdb.connect()
    c.execute(f"""CREATE TABLE snap AS SELECT ts::TIMESTAMP AS ts, stationId AS st_id, parkingBikeTotCnt::INT AS bikes, rackTotCnt::INT AS racks
                  FROM read_csv('{ROOT}/data/realtime/bikelist/*/*.csv.gz', all_varchar = true)""")
    c.execute(f"CREATE TABLE st AS SELECT st_id, st_no, role FROM '{ROOT}/analysis/out/stations.csv'")
    t_first, t_last, n_snap = c.execute("SELECT min(ts), max(ts), count(DISTINCT ts) FROM snap").fetchone()
    print(f"실시간 재고 {t_first} ~ {t_last}, 스냅샷 {n_snap}개. 트럭 사건은 {cutoff:%m-%d %H:%M}까지의 구간만 본다")

    show(c, "① 재고가 거치대 수를 넘는가 (모든 스냅샷)", """
    SELECT round(100 * avg((bikes > racks)::INT), 1) AS 거치대초과_pct,
           round(100 * avg((bikes > racks)::INT) FILTER (WHERE role = '고정 회수'), 1) AS 고정회수_초과_pct,
           round(quantile_cont(bikes / NULLIF(racks, 0), 0.9) FILTER (WHERE role = '고정 회수'), 1) AS 고정회수_재고_거치대비_p90,
           max(bikes) AS 최대재고, round(100 * avg((bikes = 0)::INT), 1) AS 빈대여소_pct
    FROM snap LEFT JOIN st USING (st_id)""")

    if cutoff <= t_first:
        print("\n② 트럭 사건: 아직 성숙한 구간이 없다. 수집이 더 쌓인 뒤 다시 돌린다")
        return
    # 대여이력: 첫 스냅샷 3시간 전부터(그 전에 빌려 구간 안에 반납한 건까지) 마지막 성숙 구간까지
    key = api_key()
    h, rows = (t_first - dt.timedelta(hours=3)).replace(minute=0, second=0, microsecond=0), []
    while h <= cutoff:
        rows += rent_hour(key, h)
        h += dt.timedelta(hours=1)
    c.execute("CREATE TABLE raw (rent_dt TIMESTAMP, rent_sid VARCHAR, rtn_dt TIMESTAMP, rtn_sid VARCHAR)")
    c.executemany("INSERT INTO raw VALUES (?, ?, ?, ?)",
                  [(r["RENT_DT"], r.get("RENT_STATION_ID"), r.get("RTN_DT") or None, r.get("RETURN_STATION_ID") or None) for r in rows])  # 반납 대여소가 없는 건은 필드가 빠져 온다
    c.execute("""CREATE TABLE tr AS SELECT * FROM raw
                 WHERE NOT (rent_sid = rtn_sid AND rtn_dt - rent_dt <= INTERVAL 1 MINUTE)""")  # 바로 반납한 건은 재고를 안 바꾼다
    c.execute(f"""
    CREATE TABLE iv AS
    WITH s AS (SELECT st_id, ts AS t1, bikes AS b1, racks, lag(ts) OVER w AS t0, lag(bikes) OVER w AS b0
               FROM snap WINDOW w AS (PARTITION BY st_id ORDER BY ts)),
    v AS (SELECT * FROM s WHERE t0 IS NOT NULL AND t1 - t0 <= INTERVAL 10 MINUTE AND t1 <= TIMESTAMP '{cutoff}'),
    rt AS (SELECT v.st_id, v.t1, count(*) AS n FROM v JOIN tr ON tr.rtn_sid = v.st_id AND tr.rtn_dt > v.t0 AND tr.rtn_dt <= v.t1 GROUP BY ALL),
    rn AS (SELECT v.st_id, v.t1, count(*) AS n FROM v JOIN tr ON tr.rent_sid = v.st_id AND tr.rent_dt > v.t0 AND tr.rent_dt <= v.t1 GROUP BY ALL)
    SELECT v.*, (b1 - b0) - (coalesce(rt.n, 0) - coalesce(rn.n, 0)) AS truck
    FROM v LEFT JOIN rt USING (st_id, t1) LEFT JOIN rn USING (st_id, t1)""")
    show(c, "② 맞춤 정도: 재고 변화 − (반납 − 대여)의 분포 (0이 많을수록 시각이 잘 맞는다)", """
    SELECT count(*) AS 구간, round(100 * avg((truck = 0)::INT), 1) AS 영_pct, round(100 * avg((abs(truck) <= 1)::INT), 1) AS 한대이내_pct,
           round(100 * avg((abs(truck) >= 3)::INT), 2) AS 세대이상_pct FROM iv""")

    # 트럭 사건: 같은 대여소에서 MERGE_MIN 안에 이어진 같은 방향 구간을 하나로 합친다
    ev = []
    for st_id, t0, t1, b0, racks, truck in c.execute(f"""SELECT st_id, t0, t1, b0, racks, truck FROM iv
                                                        WHERE abs(truck) >= {EVENT} ORDER BY st_id, t0""").fetchall():
        last = ev[-1] if ev else None
        if last and last[0] == st_id and (last[5] > 0) == (truck > 0) and t0 - last[2] <= dt.timedelta(minutes=MERGE_MIN):
            ev[-1] = (st_id, last[1], t1, last[3], racks, last[5] + truck)
        else:
            ev.append((st_id, t0, t1, b0, racks, truck))
    c.execute("CREATE TABLE ev (st_id VARCHAR, t0 TIMESTAMP, t1 TIMESTAMP, stock_before INT, racks INT, n INT)")
    c.executemany("INSERT INTO ev VALUES (?, ?, ?, ?, ?, ?)", ev)
    out = ROOT / "data/derived/truck_events.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    c.execute(f"COPY (SELECT *, CASE WHEN n > 0 THEN 'drop' ELSE 'pick' END AS kind FROM ev ORDER BY t0) TO '{out}' (HEADER)")

    show(c, f"③ 관측한 트럭 사건 (|값| ≥ {EVENT}대, {MERGE_MIN}분 안 합침). 역산: 직전 재고 배치 2대·30%가 0대, 회수 31대·96%가 거치대 이상, 옮긴 대수 13대", """
    SELECT CASE WHEN n > 0 THEN '배치' ELSE '회수' END AS 종류, count(*) AS 사건, median(abs(n)) AS 대수_중앙값,
           round(100 * avg((abs(n) BETWEEN 13 AND 15)::INT) FILTER (WHERE abs(n) >= 5), 1) AS 대수13_15_pct,
           max(abs(n)) AS 최대, median(stock_before) AS 직전재고_중앙값, round(100 * avg((stock_before = 0)::INT), 1) AS 직전0대_pct,
           round(100 * avg((stock_before >= racks)::INT), 1) AS 직전거치대이상_pct
    FROM ev GROUP BY 1 ORDER BY 1""")
    show(c, "④ 시각별 트럭 사건 수 (역산: 07–10시, 13–16시, 19–20시)", """
    SELECT hour(t0) AS 시, count(*) FILTER (WHERE n > 0) AS 배치, count(*) FILTER (WHERE n < 0) AS 회수 FROM ev GROUP BY 1 ORDER BY 1""")
    print(f"\n저장: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
