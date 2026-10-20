"""재배치 역산 15단계: 서울월드컵경기장(상암) 대형 행사 5건의 대여·반납·재배치·재고 (팀 프로젝트 탐색용 임시 파일)

무엇을: 경기장 반경 700m 대여소 12곳에서, 행사일과 같은 요일 앞뒤 4주(행사일 제외)를 비교한다.
        ① 행사 전 3시간 반납 / 행사 후 2시간 대여 ② 트럭: 행사 전 배치(미리 채우기), 행사일·다음 날 오전 회수
        ③ 복원 재고(04_stock 방식): 행사 시작 시각의 재고 합, 행사 후 2시간 동안 재고 0이 된 대여소 수
행사 날짜·시각: 웹 확인(서울시 미디어허브, 언론 보도). 축구는 20시 시작 → 끝 약 22시로 본다.
입력: 대여이력 2025(읽기만), out/gaps_2025.parquet, out/visits_2025.parquet, out/bike_pick·bike_drop.parquet, station_master.csv
출력: 화면
"""
from pathlib import Path

import duckdb

HERE = Path(__file__).parent
O = HERE / "out"
R = "/Users/junyeop/dareungi"
EVENTS = [("2025-05-11", "서울자전거대행진(도착)", 12, 8, 12), ("2025-06-10", "한국-쿠웨이트", 20, 17, 22),
          ("2025-07-31", "FC서울-바르셀로나", 20, 17, 22), ("2025-08-03", "토트넘-뉴캐슬", 20, 17, 22),
          ("2025-11-02", "JTBC 서울마라톤(출발)", 8, 5, 8)]   # (날짜, 이름, 시작시, 반납 집계 시작시, 대여 집계 시작시)
c = duckdb.connect()
c.execute(f"""CREATE TABLE vs AS SELECT st_no FROM read_csv('{R}/data/interim/station_master.csv') s WHERE s.st_no IS NOT NULL AND
 2 * 6371000 * asin(sqrt(pow(sin(radians(s.lat - 37.5683) / 2), 2) + cos(radians(37.5683)) * cos(radians(s.lat)) * pow(sin(radians(s.lon - 126.8972) / 2), 2))) <= 700""")
P = f"read_parquet('{R}/data/processed/rent/y=2025/*/*.parquet')"
c.execute(f"""CREATE TABLE ev AS SELECT ts, x FROM (SELECT rent_st st, rent_dt ts, -1 x FROM {P} UNION ALL SELECT rtn_st, rtn_dt, 1 FROM {P} WHERE rtn_st IS NOT NULL)
WHERE st IN (SELECT st_no FROM vs)""")
c.execute(f"CREATE TABLE vv AS SELECT kind, n, lo + (hi - lo) / 2 m FROM '{O}/visits_2025.parquet' WHERE st IN (SELECT st_no FROM vs)")
# 재고 복원: 경기장 대여소만
c.execute(f"""CREATE TABLE iv AS
WITH g AS (SELECT * FROM '{O}/gaps_2025.parquet' WHERE from_st IN (SELECT st_no FROM vs) OR to_st IN (SELECT st_no FROM vs)),
p AS (SELECT bike_id, t_to, lo + (hi - lo) / 2 m FROM '{O}/bike_pick.parquet'), d AS (SELECT bike_id, t_to, lo + (hi - lo) / 2 m FROM '{O}/bike_drop.parquet')
SELECT from_st st, t_from s, t_to e FROM g WHERE NOT moved AND from_st IN (SELECT st_no FROM vs)
UNION ALL SELECT g.from_st, g.t_from, p.m FROM g JOIN p USING (bike_id, t_to) WHERE g.from_st IN (SELECT st_no FROM vs)
UNION ALL SELECT g.to_st, d.m, g.t_to FROM g JOIN d USING (bike_id, t_to) WHERE g.to_st IN (SELECT st_no FROM vs)""")
c.execute("""CREATE TABLE ser AS SELECT st, ts, sum(dx) OVER (PARTITION BY st ORDER BY ts, dx ROWS UNBOUNDED PRECEDING) stock,
  lead(ts) OVER (PARTITION BY st ORDER BY ts, dx) ts_next FROM (SELECT st, s ts, 1 dx FROM iv UNION ALL SELECT st, e, -1 FROM iv)""")
evdays = [e[0] for e in EVENTS]


def stats(d, h0, hr, hd):
    """하루 d에 대해: 반납(hr~h0시), 대여(hd~hd+2시), 행사 전 배치(당일 0시~h0), 회수(당일 h0시~다음날 12시), 재고."""
    q = f"""
    SELECT (SELECT count(*) FROM ev WHERE x = 1 AND ts >= TIMESTAMP '{d}' + INTERVAL {hr} HOUR AND ts < TIMESTAMP '{d}' + INTERVAL {h0} HOUR) rtn_before,
           (SELECT count(*) FROM ev WHERE x = -1 AND ts >= TIMESTAMP '{d}' + INTERVAL {hd} HOUR AND ts < TIMESTAMP '{d}' + INTERVAL {hd + 2} HOUR) rent_after,
           (SELECT coalesce(sum(n), 0) FROM vv WHERE kind = 'drop' AND m >= TIMESTAMP '{d}' AND m < TIMESTAMP '{d}' + INTERVAL {h0} HOUR) drop_before,
           (SELECT coalesce(sum(n), 0) FROM vv WHERE kind = 'pick' AND m >= TIMESTAMP '{d}' + INTERVAL {h0} HOUR AND m < TIMESTAMP '{d}' + INTERVAL 36 HOUR) pick_after,
           (SELECT sum(stock) FROM ser s1 WHERE ts = (SELECT max(ts) FROM ser s2 WHERE s2.st = s1.st AND s2.ts <= TIMESTAMP '{d}' + INTERVAL {hd} HOUR)) stock_at,
           (SELECT count(DISTINCT st) FROM ser WHERE stock = 0 AND ts < TIMESTAMP '{d}' + INTERVAL {hd + 2} HOUR
              AND ts_next > TIMESTAMP '{d}' + INTERVAL {hd} HOUR) n_empty"""
    return c.sql(q).fetchone()


print("대여소", c.sql("SELECT count(*) FROM vs").fetchone()[0], "곳")
print(f"{'행사':<22}{'':>6}{'행사 전 반납':>10}{'행사 후 대여':>10}{'전 배치':>8}{'후 회수':>8}{'재고(대여 시작 시각)':>16}{'빈 대여소':>8}")
for d, name, h0, hr, hd in EVENTS:
    e = stats(d, h0, hr, hd)
    base = [stats(b, h0, hr, hd) for b in c.sql(f"""SELECT strftime(d, '%Y-%m-%d') FROM (SELECT DISTINCT (TIMESTAMP '{d}' + INTERVAL (7 * k) DAY)::DATE d
            FROM range(-4, 5) t(k) WHERE k <> 0) WHERE strftime(d, '%Y-%m-%d') NOT IN {tuple(evdays)} AND d < '2026-01-01'""").fetchall() for b in [b[0]]]
    med = [sorted(x[i] or 0 for x in base)[len(base) // 2] for i in range(6)]
    print(f"{name:<22}{'행사일':>6}{e[0]:>10}{e[1]:>10}{e[2]:>8}{e[3]:>8}{e[4] or 0:>16}{e[5]:>8}")
    print(f"{'':<22}{'평소':>6}{med[0]:>10}{med[1]:>10}{med[2]:>8}{med[3]:>8}{med[4]:>16}{med[5]:>8}")
