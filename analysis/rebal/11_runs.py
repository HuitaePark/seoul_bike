"""재배치 역산 11단계: 트럭 운행 1회에 회수로 몇 곳, 배치로 몇 곳 들렀나 (팀 프로젝트 탐색용 임시 파일)

무엇을: 회수 방문과 배치 방문(02_visits)을 같은 자전거로 이은 연결 요소 = 운행 1회로 본다.
        시각 창이 넓은 방문은 날짜를 넘어 사슬처럼 이어지므로(08_loads) 창이 W시간 이하인 방문끼리만 잇고 W를 바꿔 본다.
        운행에 속한 방문의 자전거 중 하나라도 창이 넓은 방문으로 이어지면 '빠진 정류장이 있을 수 있음'으로 표시하고 따로 센다.
입력: out/gaps_2025.parquet, out/bike_pick·bike_drop.parquet / 출력: 화면
"""
from pathlib import Path

import duckdb
import networkx as nx
import pandas as pd

HERE = Path(__file__).parent
O = HERE / "out"
c = duckdb.connect()
c.execute(f"""CREATE TABLE b AS
SELECT g.bike_id, g.from_st, g.to_st,
       'P' || g.from_st || '|' || p.lo || '|' || p.hi pk, date_diff('minute', p.lo, p.hi) / 60.0 pw,
       'D' || g.to_st || '|' || d.lo || '|' || d.hi dk, date_diff('minute', d.lo, d.hi) / 60.0 dw
FROM '{O}/gaps_2025.parquet' g JOIN '{O}/bike_pick.parquet' p USING (bike_id, t_to) JOIN '{O}/bike_drop.parquet' d USING (bike_id, t_to)""")
# 방문마다 창 폭(같은 방문의 자전거는 창이 같다)
c.execute("CREATE TABLE vw AS SELECT pk k, any_value(pw) w FROM b GROUP BY 1 UNION ALL SELECT dk, any_value(dw) FROM b GROUP BY 1")

for W in (1, 2, 3):
    ok = c.sql(f"SELECT k FROM vw WHERE w <= {W}").df().k
    okset = set(ok)
    e = c.sql(f"SELECT pk, dk, count(*) n FROM b WHERE pw <= {W} AND dw <= {W} GROUP BY ALL").df()
    # 창이 좁은 방문인데 자전거 일부가 넓은 방문으로 이어지면 그 운행은 정류장이 빠졌을 수 있다
    leak = set(c.sql(f"SELECT pk FROM b WHERE pw <= {W} AND dw > {W} UNION SELECT dk FROM b WHERE dw <= {W} AND pw > {W}").df().iloc[:, 0])
    G = nx.Graph()
    G.add_weighted_edges_from(e.itertuples(index=False, name=None))
    rows = []
    for cc in nx.connected_components(G):
        sub = G.subgraph(cc)
        rows.append({"pick": sum(n.startswith("P") for n in cc), "drop": sum(n.startswith("D") for n in cc),
                     "bikes": int(sub.size(weight="weight")), "complete": not (cc & leak)})
    r = pd.DataFrame(rows)
    good = r[r.complete & (r.bikes >= 5) & (r.bikes <= 30)]
    cover = e.n.sum() / c.sql("SELECT count(*) FROM b").fetchone()[0]
    print(f"\n== 창 {W}시간 이하: 운행 {len(r):,}개(자전거 {cover:.0%} 포함), 그중 빠짐없는 5~30대 운행 {len(good):,}개"
          f" / 31대 넘는 운행 {100 * (r.bikes > 30).mean():.1f}%")
    print(f"   회수 정류장 수: 평균 {good.pick.mean():.2f}, 1곳 {100 * (good.pick == 1).mean():.0f}%, 2곳 {100 * (good.pick == 2).mean():.0f}%, 3곳+ {100 * (good.pick >= 3).mean():.0f}%")
    print(f"   배치 정류장 수: 평균 {good['drop'].mean():.2f}, 1곳 {100 * (good['drop'] == 1).mean():.0f}%, 2곳 {100 * (good['drop'] == 2).mean():.0f}%, 3곳+ {100 * (good['drop'] >= 3).mean():.0f}%")
    print(f"   운행당 자전거 중앙값 {good.bikes.median():.0f}대, 정류장 합 중앙값 {(good.pick + good['drop']).median():.0f}곳")
    tab = pd.crosstab(good.pick.clip(upper=3), good["drop"].clip(upper=4), normalize=True).mul(100).round(1)
    tab.index = [f"회수 {i}{'+' if i == 3 else ''}곳" for i in tab.index]
    tab.columns = [f"배치 {j}{'+' if j == 4 else ''}곳" for j in tab.columns]
    print(tab.to_string())
