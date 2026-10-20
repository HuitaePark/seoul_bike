"""재배치 역산 8단계: 트럭 '적재 1회'(회수 방문 → 배치 방문) 복원 (팀 프로젝트 탐색용 임시 파일)

무엇을: 재배치 후보 자전거마다 회수 방문(원래 대여소, 시각 창)과 배치 방문(새 대여소, 시각 창)이 있다(02_visits).
        같은 자전거를 공유하는 회수·배치 방문을 이어 연결 요소를 만들면, 함께 실려 함께 내려진 묶음 = 적재 1회가 된다.
        개별 트럭 번호는 공개 데이터에 없으므로 '적재'까지가 관측 가능한 최소 단위다.
입력: out/gaps_2025.parquet, out/bike_pick·bike_drop.parquet, station_master.csv / 출력: out/loads_2025.parquet, out/load_edges_2025.parquet
"""
from pathlib import Path

import duckdb
import networkx as nx

HERE = Path(__file__).parent
O = HERE / "out"
R = "/Users/junyeop/dareungi"
NORTH = ("종로구", "중구", "용산구", "성동구", "광진구", "동대문구", "중랑구", "성북구", "강북구", "도봉구", "노원구",
         "은평구", "서대문구", "마포구")
c = duckdb.connect()
c.execute(f"""CREATE TABLE s AS SELECT st_no, district, lat, lon, CASE WHEN district IN {NORTH} THEN '강북' ELSE '강남' END rg
              FROM read_csv('{R}/data/interim/station_master.csv') WHERE st_no IS NOT NULL AND district LIKE '%구' AND district <> '분당구'""")
# 자전거 한 대 = 회수 방문 키(대여소, 창) → 배치 방문 키(대여소, 창)
c.execute(f"""CREATE TABLE b AS
SELECT g.bike_id, g.from_st, g.to_st, p.lo p_lo, p.hi p_hi, d.lo d_lo, d.hi d_hi,
       'P' || g.from_st || '|' || p.lo || '|' || p.hi pk, 'D' || g.to_st || '|' || d.lo || '|' || d.hi dk
FROM '{O}/gaps_2025.parquet' g JOIN '{O}/bike_pick.parquet' p USING (bike_id, t_to) JOIN '{O}/bike_drop.parquet' d USING (bike_id, t_to)""")
print("자전거 수", c.sql("SELECT count(*), count(DISTINCT pk), count(DISTINCT dk) FROM b").fetchone())

G = nx.Graph()
G.add_edges_from(c.sql("SELECT DISTINCT pk, dk FROM b").fetchall())
comp = {n: i for i, cc in enumerate(nx.connected_components(G)) for n in cc}
c.register("cm", __import__("pandas").DataFrame({"k": list(comp), "load_id": list(comp.values())}))
c.execute("CREATE TABLE bl AS SELECT b.*, cm.load_id FROM b JOIN cm ON cm.k = b.pk")
c.execute("""CREATE TABLE loads AS
SELECT load_id, count(*) bikes, count(DISTINCT pk) n_pick, count(DISTINCT dk) n_drop,
       count(DISTINCT from_st) n_pick_st, count(DISTINCT to_st) n_drop_st,
       max(p_lo) t_pick_lo, min(d_hi) t_drop_hi, min(p_lo) t_first, max(d_hi) t_last,
       count(DISTINCT a.rg) + count(DISTINCT z.rg) - 1 AS n_rg_raw,
       bool_and(a.rg = z.rg) same_rg, any_value(a.rg) rg, mode(a.district) gu
FROM bl JOIN s a ON a.st_no = bl.from_st JOIN s z ON z.st_no = bl.to_st GROUP BY 1""")
c.execute(f"COPY loads TO '{O}/loads_2025.parquet'")
c.execute(f"COPY (SELECT load_id, from_st, to_st, count(*) n, min(p_lo) p_lo, max(d_hi) d_hi FROM bl GROUP BY ALL) TO '{O}/load_edges_2025.parquet'")

print(c.sql("""SELECT count(*) loads, round(avg(bikes), 1) mean_bikes, quantile_cont(bikes, [.5, .9, .99]) bikes_q,
  round(100 * avg((n_pick = 1 AND n_drop = 1)::INT), 1) pct_1to1, round(100 * avg((n_pick <= 2 AND n_drop <= 2)::INT), 1) pct_le2,
  max(n_pick) max_pick, max(n_drop) max_drop, round(100 * avg(same_rg::INT), 2) pct_one_region FROM loads"""))
print(c.sql("""SELECT CASE WHEN bikes = 1 THEN 'a 1' WHEN bikes <= 4 THEN 'b 2~4' WHEN bikes <= 15 THEN 'c 5~15' WHEN bikes <= 30 THEN 'd 16~30' ELSE 'e 31+' END k,
  count(*) loads, sum(bikes) bikes, round(avg(n_pick_st), 1) pick_st, round(avg(n_drop_st), 1) drop_st,
  round(median(date_diff('minute', t_first, t_last)) / 60, 1) med_span_h FROM loads GROUP BY 1 ORDER BY 1"""))
