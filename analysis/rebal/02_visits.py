"""재배치 역산 2단계: 옮겨진 자전거를 '트럭 방문(회수·배치)'으로 묶는다. (팀 프로젝트 탐색용 임시 파일)

무엇을: 재배치 후보(아래 기준) 자전거를 대여소별로, 옮긴 시각 창 [t_from, t_to]가 한 시점에서 겹치는 것끼리 묶는다.
        구간 찌르기(interval stabbing) 탐욕법: 창 끝(t_to) 순으로 정렬해 첫 끝 시각에 점을 찍고 그 점을 포함하는 창을 모두 한 방문으로 본다.
        → 자료와 모순되지 않는 '최소 방문 횟수'와 방문당 대수(위쪽 추정)를 준다. 방문 시각은 [묶음의 max t_from, 점] 사이.
왜: 방문 단위여야 '정류장당 몇 대', '하루 몇 번 방문', '몇 시에 일하나'를 말할 수 있다.
재배치 후보(임시 기준): 다른 대여소 & 구간 안 고장신고 없음 & 공백 72시간 미만 & 직선거리 150m ~ 10km
입력: out/gaps_<연도>.parquet / 출력: out/visits_<연도>.parquet (kind = drop 배치 | pick 회수), out/bike_pick·bike_drop(2025 외 연도는 _<연도>)
실행: python 02_visits.py [연도, 기본 2025]
"""
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

HERE = Path(__file__).parent
YEAR = sys.argv[1] if len(sys.argv) > 1 else "2025"
SUF = "" if YEAR == "2025" else f"_{YEAR}"
c = duckdb.connect()
mv = c.sql(f"""SELECT bike_id, from_st, to_st, t_from, t_to, dist_m FROM '{HERE}/out/gaps_{YEAR}.parquet'
              WHERE moved AND NOT had_report AND gap_h < 72 AND dist_m BETWEEN 150 AND 10000""").df()
print("재배치 후보", len(mv))


def stab(df, st_col):
    """대여소별 최소 방문 묶음 번호를 붙인다(창 끝 순 정렬 → 점보다 늦게 시작하는 창에서 새 방문)."""
    df = df.sort_values([st_col, "t_to"]).reset_index(drop=True)
    st, lo, hi = df[st_col].to_numpy(), df["t_from"].to_numpy(), df["t_to"].to_numpy()
    gid = np.empty(len(df), dtype=np.int64)
    g, point, prev = -1, None, None
    for i in range(len(df)):
        if st[i] != prev or lo[i] > point:
            g, point, prev = g + 1, hi[i], st[i]
        gid[i] = g
    df["visit"] = gid
    return df


rows = []
for kind, col in [("drop", "to_st"), ("pick", "from_st")]:
    d = stab(mv, col)
    v = d.groupby("visit").agg(st=(col, "first"), n=("bike_id", "size"),
                               lo=("t_from", "max"), hi=("t_to", "min"),
                               n_partner=("from_st" if kind == "drop" else "to_st", "nunique")).reset_index(drop=True)
    v["kind"] = kind
    rows.append(v)
    # 자전거별 방문 창(재고 복원용): 키는 (bike_id, t_to)
    d = d.merge(v[["lo", "hi"]], left_on="visit", right_index=True)
    d[["bike_id", "t_to", "lo", "hi"]].to_parquet(HERE / "out" / f"bike_{kind}{SUF}.parquet")
vis = pd.concat(rows)
assert (vis["lo"] <= vis["hi"]).all()                  # 묶음 안 모든 창이 한 시점을 공유해야 한다
vis["width_h"] = (vis["hi"] - vis["lo"]).dt.total_seconds() / 3600
vis.to_parquet(HERE / "out" / f"visits_{YEAR}.parquet")
print(vis.groupby("kind").agg(visits=("n", "size"), bikes=("n", "sum"), mean_n=("n", "mean"),
                              med_width_h=("width_h", "median")))
