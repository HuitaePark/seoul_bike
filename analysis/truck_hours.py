# Author: Shin Junyeop
# Date: 21OCT2026
# Purpose: 실시간 재고로 관측한 트럭 사건을 구역·시간대별로 모아, 실제 근무 시각과 하루에 움직인 트럭 수(하한)를 추정한다.

"""실제 근무 시간·트럭 수 추정 (자원을 맞춘 비교의 입력: sim --shifts, --trucks).

입력: data/derived/truck_events.csv (analysis/validate_stock.py가 만든다. 먼저 돌린다),
      analysis/out/stations.csv (st_id → 구역·좌표), analysis/out/ref_regions.csv (역산의 바쁜 날 동시 트럭 하한, 비교용)
출력: data/derived/truck_hours.csv (구역 × 날짜 × 시: 사건 수, 회수·배치 대수, 방문 대여소 수), 화면 요약
트럭 수 추정: 구역·날짜마다 사건을 시각순으로 놓고, 한 트럭이 사건 i 다음에 사건 j를 할 수 있으면 i → j로 잇는다.
  할 수 있다 = i의 이른 시각(t0) + 이동 시간 ≤ j의 늦은 시각(t1). 이동 시간은 시뮬레이터와 같다(직선 × DETOUR, SPEED_KMH).
  모든 사건을 덮는 최소 트럭 수 = 사건 수 − 최대 이음 수(이분 매칭, 최소 경로 덮개)다. 사건 시각은 5분 스냅샷 사이로만 알아
  가장 유리하게 이었으므로 하한이다. 이은 사슬 하나를 트럭 하나로 보고 사슬의 첫 t0 ~ 마지막 t1을 근무 중으로 세면
  시간대별 동시 트럭 수(하한)가 나온다.
한계: 구역 경계를 넘나드는 트럭과 EVENT 미만의 작은 사건은 빠진다. 거짓 사건(대여·반납 시각 어긋남)은 트럭 수를 늘린다.
실행: .venv/bin/python analysis/validate_stock.py && .venv/bin/python analysis/truck_hours.py
"""
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sim.engine import DETOUR, SHIFTS, SPEED_KMH  # noqa: E402  (sim이 rebalancing 경로도 연다)
from rebalancing.geo import haversine_m  # noqa: E402


def min_trucks(events: list[tuple]) -> list[list[int]]:
    """events: (t0, t1, lat, lon) 목록. 모든 사건을 덮는 최소 개수의 사슬(트럭마다 사건 번호 목록)을 돌려준다."""
    order = sorted(range(len(events)), key=lambda i: (events[i][0], i))
    rank = {i: k for k, i in enumerate(order)}

    def can(i, j):
        (a0, _, la, lo), (_, b1, lb, lob) = events[i], events[j]
        travel = timedelta(minutes=haversine_m(la, lo, lb, lob) * DETOUR / (SPEED_KMH * 1000 / 60))
        return rank[i] < rank[j] and a0 + travel <= b1

    nxt = {i: [j for j in order if can(i, j)] for i in order}
    match_to: dict[int, int] = {}  # 사건 j → 그 앞에 이은 사건 i

    def augment(i, seen):
        for j in nxt[i]:
            if j not in seen:
                seen.add(j)
                if j not in match_to or augment(match_to[j], seen):
                    match_to[j] = i
                    return True
        return False

    for i in order:
        augment(i, set())
    after = {i: j for j, i in match_to.items()}
    chains = []
    for i in order:
        if i in match_to:  # 다른 사건 뒤에 이어진 사건은 사슬의 시작이 아니다
            continue
        chains.append([i])
        while chains[-1][-1] in after:
            chains[-1].append(after[chains[-1][-1]])
    return chains


def in_shift(hour: int) -> bool:
    return any(a <= hour < b for a, b in SHIFTS)


def main() -> None:
    ev = pd.read_csv(ROOT / "data/derived/truck_events.csv", parse_dates=["t0", "t1"])
    st = pd.read_csv(ROOT / "analysis/out/stations.csv", usecols=["st_id", "terr", "lat", "lon"]).dropna(subset=["st_id", "terr"])
    ev = ev.merge(st, on="st_id", how="left")
    n_out = ev.terr.isna().sum()
    ev = ev.dropna(subset=["terr"])
    ev["date"], ev["hour"], ev["bikes"] = ev.t0.dt.date, ev.t0.dt.hour, ev.n.abs()
    print(f"트럭 사건 {len(ev):,}건 ({ev.t0.min():%m-%d %H:%M} ~ {ev.t1.max():%m-%d %H:%M}), 구역을 모르는 대여소 사건 {n_out}건은 뺐다")

    hours = ev.groupby(["terr", "date", "hour"]).agg(events=("n", "size"), picked=("n", lambda x: -x[x < 0].sum()),
                                                     dropped=("n", lambda x: x[x > 0].sum()), stations=("st_id", "nunique")).reset_index()
    out = ROOT / "data/derived/truck_hours.csv"
    hours.to_csv(out, index=False)

    span = ev.groupby(["terr", "date"]).agg(사건=("n", "size"), 첫_사건=("t0", "min"), 마지막_사건=("t1", "max"))
    span["첫_사건"], span["마지막_사건"] = span.첫_사건.dt.strftime("%H:%M"), span.마지막_사건.dt.strftime("%H:%M")
    print("\n== 구역·날짜별 첫·마지막 사건 (첫날·마지막 날은 수집 시작·끝에서 잘린다)\n" + span.to_string())

    inside = ev[ev.hour.map(in_shift)].bikes.sum() / ev.bikes.sum()
    by_block = ev.groupby(pd.cut(ev.hour, [0, 7, 10, 13, 16, 19, 21, 24], right=False)).bikes.sum() / ev.bikes.sum()
    print(f"\n== 옮긴 대수 중 우리 근무조(07–10·13–16·19–21) 밖: {1 - inside:.0%} (역산은 38%)")
    print(by_block.rename(lambda iv: f"{iv.left:02d}–{iv.right:02d}시").round(3).to_string())

    ref = pd.read_csv(ROOT / "analysis/out/ref_regions.csv").set_index("name").trucks_min
    rows = []
    for (terr, d), g in ev.groupby(["terr", "date"]):
        g = g.reset_index(drop=True)
        chains = min_trucks(list(zip(g.t0, g.t1, g.lat, g.lon)))
        busy = [(g.t0[c].min(), g.t1[c].max()) for c in chains]
        peak = max(sum(a < pd.Timestamp(d) + timedelta(hours=h + 1) and b > pd.Timestamp(d) + timedelta(hours=h) for a, b in busy)
                   for h in range(24))
        rows.append(dict(terr=terr, date=d, 사건=len(g), 하루_트럭_하한=len(chains), 동시_트럭_하한_최대=peak, 역산_trucks_min=ref.get(terr)))
    print("\n== 트럭 수 추정 (이동 시간으로 이은 최소 사슬 수)\n" + pd.DataFrame(rows).set_index(["terr", "date"]).to_string())
    print(f"\n저장: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
