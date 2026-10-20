"""재배치 역산 9단계: 트럭(운영 조)의 활동 구역 추정 (팀 프로젝트 탐색용 임시 파일)

무엇을: 트럭 번호는 공개 데이터에 없고, 적재 1회 단위 연결은 시각 창이 넓어 사슬처럼 번진다(08_loads).
        대신 대여소 A → B로 재배치된 대수를 선으로 하는 대여소 연결망을 권역(강북·강남)별로 만들고,
        서로 자주 주고받는 대여소 묶음(Louvain 군집)을 '활동 구역' 후보로 본다. 구역 안에서 끝나는 이동 비율이 높을수록
        한 트럭(조)이 그 구역을 맡는다는 해석이 그럴듯해진다. 자치구를 구역으로 썼을 때와 비교한다.
입력: out/gaps_2025.parquet, station_master.csv / 출력: out/territory.csv(대여소별 구역), out/territory_map.png, 화면
"""
from pathlib import Path

import duckdb
import matplotlib.pyplot as plt
import networkx as nx
import pandas as pd

HERE = Path(__file__).parent
O = HERE / "out"
R = "/Users/junyeop/dareungi"
NORTH = ("종로구", "중구", "용산구", "성동구", "광진구", "동대문구", "중랑구", "성북구", "강북구", "도봉구", "노원구",
         "은평구", "서대문구", "마포구")
c = duckdb.connect()
c.execute(f"""CREATE TABLE s AS SELECT st_no, st_name, district, lat, lon, CASE WHEN district IN {NORTH} THEN '강북' ELSE '강남' END rg
              FROM read_csv('{R}/data/interim/station_master.csv') WHERE st_no IS NOT NULL AND district LIKE '%구' AND district <> '분당구'""")
c.execute(f"""CREATE TABLE mv AS SELECT g.from_st a, g.to_st b, g.t_to, sa.rg rg, sa.district ga, sb.district gb
FROM '{O}/gaps_2025.parquet' g JOIN s sa ON sa.st_no = g.from_st JOIN s sb ON sb.st_no = g.to_st
WHERE moved AND NOT had_report AND gap_h < 72 AND dist_m BETWEEN 150 AND 10000 AND sa.rg = sb.rg""")
ed = c.sql("SELECT rg, least(a, b) u, greatest(a, b) v, count(*) w FROM mv GROUP BY ALL").df()


def communities(rg, res, seed):
    G = nx.Graph()
    G.add_weighted_edges_from(ed.loc[ed.rg == rg, ["u", "v", "w"]].itertuples(index=False, name=None))
    return [sorted(cm) for cm in nx.community.louvain_communities(G, weight="weight", resolution=res, seed=seed)]


def inside_share(lab):
    """구역 안에서 시작하고 끝나는 재배치 비율(대수 가중)."""
    m = ed.assign(cu=ed.u.map(lab), cv=ed.v.map(lab))
    return (m.w * (m.cu == m.cv)).sum() / m.w.sum()


# 안정성: 해상도·시드를 바꿔 구역 수와 내부 비율을 본다
print("== 설정별 구역 수와 구역 안 재배치 비율")
for res in (0.5, 1.0, 2.0):
    for seed in (1, 2, 3):
        lab = {}
        n = {}
        for rg in ("강북", "강남"):
            cms = communities(rg, res, seed)
            n[rg] = sum(len(cm) >= 10 for cm in cms)
            lab.update({st: f"{rg}{i}" for i, cm in enumerate(cms) for st in cm})
        print(f"해상도 {res} 시드 {seed}: 강북 {n['강북']}개, 강남 {n['강남']}개(10곳 이상), 구역 안 비율 {100 * inside_share(lab):.1f}%")
gu = dict(c.sql("SELECT st_no, district FROM s").fetchall())
print(f"비교 — 자치구를 구역으로: 25개, 구역 안 비율 {100 * inside_share(gu):.1f}%")

# 기준 설정(해상도 1, 시드 1)으로 구역별 요약
lab = {}
for rg in ("강북", "강남"):
    cms = sorted(communities(rg, 1.0, 1), key=len, reverse=True)
    lab.update({st: f"{rg}-{i + 1:02d}" for i, cm in enumerate(cms) for st in cm})
c.register("lab", pd.DataFrame({"st_no": list(lab), "terr": list(lab.values())}))
c.execute(f"COPY (SELECT s.*, terr FROM s JOIN lab USING (st_no)) TO '{O}/territory.csv'")
print("\n== 구역별 요약(해상도 1, 4~10월 하루 평균)")
summary = c.sql("""
WITH m AS (SELECT mv.*, la.terr ta, lb.terr tb FROM mv JOIN lab la ON la.st_no = mv.a JOIN lab lb ON lb.st_no = mv.b),
st AS (SELECT terr, count(*) n_st FROM lab GROUP BY 1),
gs AS (SELECT terr, district, count(*) n, row_number() OVER (PARTITION BY terr ORDER BY count(*) DESC) r FROM lab JOIN s USING (st_no) GROUP BY terr, district),
gu AS (SELECT terr, string_agg(district || '(' || n || ')', ' ' ORDER BY n DESC) main_gu FROM gs WHERE r <= 3 GROUP BY 1),
flow AS (SELECT ta terr, count(*) FILTER (WHERE t_to BETWEEN '2025-04-01' AND '2025-11-01') / 214.0 out_day,
                round(100 * avg((ta = tb)::INT), 1) pct_inside FROM m GROUP BY 1)
SELECT terr, n_st, round(out_day) bikes_day, round(out_day / 13, 1) loads_day, pct_inside, main_gu
FROM st JOIN flow USING (terr) JOIN gu USING (terr) WHERE n_st >= 10 ORDER BY terr""").df()
print(summary.to_string(index=False))

# 지도: 구역마다 색
d = c.sql("SELECT s.lat, s.lon, terr FROM s JOIN lab USING (st_no) WHERE lat IS NOT NULL").df()
plt.rcParams["font.family"] = "AppleGothic"
fig, ax = plt.subplots(figsize=(11, 9))
for i, (t, g) in enumerate(d.groupby("terr")):
    ax.scatter(g.lon, g.lat, s=6, color=plt.cm.tab20(i % 20), label=t if len(g) >= 10 else None)
    if len(g) >= 10:
        ax.annotate(t, (g.lon.median(), g.lat.median()), fontsize=8, weight="bold")
ax.set_title("재배치 흐름으로 묶은 활동 구역 후보(2025, Louvain, 강북·강남 따로)")
ax.set_aspect(1 / 0.79)
fig.savefig(O / "territory_map.png", dpi=110, bbox_inches="tight")
print("\n지도:", O / "territory_map.png")
