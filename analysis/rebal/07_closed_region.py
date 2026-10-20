"""재배치 역산 7단계: 강북·강남을 '닫힌 권역'으로 풀어도 되는가 (팀 프로젝트 탐색용 임시 파일)

무엇을: ① 물량 — 이용자가 강을 건너며 옮긴 순대수가 날마다 쌓이면 얼마나 되는지(누적 최대치)
        ② 거리 — 대여소별 하루 평균 순반납(반납 − 대여, 4~10월)을 남는 곳 → 모자란 곳으로 최소 자전거·km로 짝지을 때,
           한강을 건널 수 있게 한 경우와 권역 안에서만 짝짓게 한 경우의 총 이동량 비교(최소비용 흐름)
주의: 반납 대여소가 비어 있는 대여(2025년 0.5%)는 양쪽 끝을 모두 빼야 남는/모자람이 한쪽으로 쏠리지 않는다.
입력: 본 프로젝트 대여이력 2025·station_master.csv(읽기만) / 출력: 화면
"""
from pathlib import Path

import duckdb
import networkx as nx
import numpy as np
from sklearn.neighbors import BallTree

HERE = Path(__file__).parent
R = "/Users/junyeop/dareungi"
NORTH = ("종로구", "중구", "용산구", "성동구", "광진구", "동대문구", "중랑구", "성북구", "강북구", "도봉구", "노원구",
         "은평구", "서대문구", "마포구")
c = duckdb.connect()
c.execute(f"""CREATE TABLE s AS SELECT st_no, district, lat, lon, CASE WHEN district IN {NORTH} THEN '강북' ELSE '강남' END rg
              FROM read_csv('{R}/data/interim/station_master.csv') WHERE st_no IS NOT NULL AND district LIKE '%구' AND district <> '분당구'""")
c.execute(f"""CREATE TABLE ride AS SELECT t.rent_dt, a.st_no fs, b.st_no ts_, a.rg rf, b.rg rt
FROM read_parquet('{R}/data/processed/rent/y=2025/*/*.parquet') t JOIN s a ON a.st_no = t.rent_st JOIN s b ON b.st_no = t.rtn_st""")
o = duckdb.connect(f"{R}/data/db/ops.duckdb", read_only=True)
c.register("h", o.sql("SELECT date FROM holidays WHERE year(date) = 2025").df())
o.close()
c.execute("CREATE TABLE hol AS SELECT date::DATE d FROM h")

# ---------- ① 물량: 날마다 강북→강남 순대수와 그 누적
net = c.sql("""SELECT rent_dt::DATE d, sum(CASE WHEN rf = '강북' AND rt = '강남' THEN 1 WHEN rf = '강남' AND rt = '강북' THEN -1 ELSE 0 END) n2s
               FROM ride GROUP BY 1 ORDER BY 1""").df()
cum = net["n2s"].cumsum()
season = net["d"].astype(str).between("2025-04-01", "2025-10-31")
cum_s = net.loc[season, "n2s"].cumsum()
print("== ① 이용자 순이동 누적(강북→강남, +면 강남에 쌓임)")
print(f"연간 누적 최댓값 {cum.max():.0f}, 최솟값 {cum.min():.0f}, 연말 {cum.iloc[-1]:.0f}")
print(f"4~10월 누적 최댓값 {cum_s.max():.0f}, 최솟값 {cum_s.min():.0f}, 10월 말 {cum_s.iloc[-1]:.0f}")
wk = net.assign(w=net["d"].astype("datetime64[ns]").dt.to_period("W"))[season].groupby("w")["n2s"].sum()
print(f"4~10월 주간 순이동: 중앙값 {wk.median():.0f}, 최대 {wk.max():.0f}, 최소 {wk.min():.0f}, 강남 쪽(+) 주 비율 {100 * (wk > 0).mean():.0f}%")

# ---------- ② 거리: 대여소별 하루 평균 순반납 → 최소비용 짝짓기
st = c.sql("""
WITH e AS (SELECT ts_ st, rent_dt t, 1 x FROM ride UNION ALL SELECT fs, rent_dt, -1 FROM ride)
SELECT s.st_no, s.rg, s.lat, s.lon,
       sum(x) FILTER (WHERE NOT is_off) / count(DISTINCT d) FILTER (WHERE NOT is_off) wd,
       sum(x) FILTER (WHERE is_off) / count(DISTINCT d) FILTER (WHERE is_off) od
FROM (SELECT st, x, t::DATE d, (isodow(t) >= 6 OR t::DATE IN (SELECT d FROM hol)) AS is_off FROM e WHERE month(t) BETWEEN 4 AND 10) e
JOIN s ON s.st_no = e.st WHERE s.lat IS NOT NULL GROUP BY ALL""").df()
# shortcut: 대여소 평균 하루 순반납만 본다(날마다 다른 불균형·하루 안 시간차는 반영 안 함). 근무조 단위가 필요하면 날·구간별로 다시 푼다.
xyz = np.radians(st[["lat", "lon"]].to_numpy())
tree = BallTree(xyz, metric="haversine")
K = 800


def solve(col, split):
    """남는 대여소 → 모자란 대여소 최소 자전거·km. split이면 같은 권역끼리만 잇는다. 못 채운 양은 비용이 큰 가상 노드가 낸다."""
    q = np.round(st[col].to_numpy() * 10).astype(int)                  # 0.1대 단위 정수
    G = nx.DiGraph()
    dist, idx = tree.query(xyz, k=K)
    for i in np.where(q > 0)[0]:
        for d_, j in zip(dist[i], idx[i]):
            if q[j] < 0 and (not split or st.rg.iat[i] == st.rg.iat[j]):
                G.add_edge(i, j, weight=int(d_ * 6371000), capacity=int(q[i]))
    for i in np.where(q != 0)[0]:
        G.add_node(i, demand=-int(q[i]))                                  # networkx: 공급 = 음수 demand
    rgs = ["강북", "강남"] if split else ["all"]
    for rg in rgs:                                                        # 권역마다 남는 것은 '그대로 둠'(0원), 모자람은 '못 채움'(벌점)
        mine = [i for i in np.where(q != 0)[0] if split is False or st.rg.iat[i] == rg]
        sup = sum(int(q[i]) for i in mine if q[i] > 0)
        dem = -sum(int(q[i]) for i in mine if q[i] < 0)
        keep, unmet = f"keep_{rg}", f"unmet_{rg}"
        G.add_node(keep, demand=sup)                                      # 남는 것 전부를 둘 수 있고
        G.add_node(unmet, demand=-dem)                                    # 모자란 것 전부를 못 채울 수 있다
        G.add_edge(unmet, keep, weight=0)                                 # 쓰지 않은 '못 채움'은 그대로 흘려보낸다
        for i in mine:
            if q[i] > 0:
                G.add_edge(i, keep, weight=0)
            else:
                G.add_edge(unmet, i, weight=50_000)                       # 50km 벌점: 실제로 짝지을 곳이 없을 때만 쓴다
    flow = nx.min_cost_flow(G)
    km = cross = moved = unmet_n = 0
    for u, vs in flow.items():
        for v, f in vs.items():
            if f and isinstance(u, (int, np.integer)) and isinstance(v, (int, np.integer)):
                m = G[u][v]["weight"]
                km += f * m / 1000 / 10
                moved += f / 10
                cross += f / 10 if st.rg.iat[u] != st.rg.iat[v] else 0
            elif f and str(u).startswith("unmet") and isinstance(v, (int, np.integer)):
                unmet_n += f / 10
    return km, moved, cross, unmet_n


print("\n== ② 남는 곳 → 모자란 곳 최소 자전거·km(4~10월 하루 평균 순반납 기준)")
for col, name in [("wd", "평일"), ("od", "주말·공휴일")]:
    v = st[col].to_numpy()
    print(f"[{name}] 남는 합 {v[v > 0].sum():.0f}대, 모자란 합 {-v[v < 0].sum():.0f}대 / 권역 순반납 강북 {st.loc[st.rg == '강북', col].sum():+.0f}, 강남 {st.loc[st.rg == '강남', col].sum():+.0f}")
    a, b = solve(col, False), solve(col, True)
    print(f"  강 건너기 허용: 짝지음 {a[1]:.0f}대, 평균 {a[0] / a[1]:.2f}km, 그중 강 건넘 {a[2]:.0f}대({100 * a[2] / a[1]:.1f}%), 못 채움 {a[3]:.0f}대")
    print(f"  권역 안에서만: 짝지음 {b[1]:.0f}대, 평균 {b[0] / b[1]:.2f}km, 못 채움 {b[3]:.0f}대(+{b[3] - a[3]:.0f})")
