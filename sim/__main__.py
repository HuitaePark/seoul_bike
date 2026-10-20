# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: 구역 하나의 하루를 정책마다 재생하고 지표를 표로 출력한다. 현행 운영 재현은 다음 날 0시 복원 재고와 맞는지도 본다.

"""실행: .venv/bin/python -m sim --terr 강남-05 --date 2025-06-11 [--trucks 3]

지표는 둘 다 보고하고 판단은 실패 대여로 한다(2026-10-20 결정). 빈 대여소-분은 이용이 적은 대여소를 채워도 줄어든다.
"""
import argparse
from datetime import date, timedelta

from .engine import load_day, replay
from .policies import CurrentOps, NoRebalancing, SimpleRule


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--terr", default="강남-05")
    ap.add_argument("--date", type=date.fromisoformat, default=date(2025, 6, 11))
    ap.add_argument("--trucks", type=int, default=3)  # 역산 7절: 강남-05 바쁜 날 동시 트럭 하한
    args = ap.parse_args()

    day = load_day(args.terr, args.date)
    # 이름은 공개 API의 Policy 값과 같다(docs/specs/openapi.yaml). AGENT(본 시스템)는 M5에 더한다
    policies = {"NONE": NoRebalancing(), "GREEDY": SimpleRule(day.fixed_pickup), "CURRENT_OPS": CurrentOps()}
    print(f"{args.terr} {args.date}  대여소 {len(day.stations)}곳  초기 재고 {sum(s.bikes for s in day.stations.values())}대  "
          f"대여 기록 {len(day.trips)}건  역산 방문 {len(day.visits)}건  트럭 {args.trucks}대\n")
    print(f"{'정책':12} {'실패 대여':>8} {'빈 대여소-분':>12} {'과적재-분':>10} {'시도 대여':>8} {'옮긴 대수':>8} {'덜 실음':>6} {'시간당 대수':>10}")
    results = {}
    for name, policy in policies.items():
        r = results[name] = replay(day, policy, trucks=args.trucks)
        print(f"{name:12} {r.failed:8,} {r.empty_min:12,.0f} {r.over_min:10,.0f} {r.rentals:8,} {r.moved:8,} {r.pick_short:6,} {r.moved_per_truck_h:10.1f}")

    # 검증: 현행 운영 재현으로 하루를 돌린 끝 재고는 다음 날 0시에 복원한 재고와 같아야 한다(같은 역산 기준을 쓰므로)
    nxt = load_day(args.terr, args.date + timedelta(days=1)).stations
    end = results["CURRENT_OPS"].stock
    common = end.keys() & nxt.keys()
    gap = [abs(end[s] - nxt[s].bikes) for s in common]
    print(f"\n검증(CURRENT_OPS 하루 끝 재고 vs 다음 날 0시 복원 재고, {len(common)}곳): "
          f"합계 {sum(end[s] for s in common):,} vs {sum(nxt[s].bikes for s in common):,}대, "
          f"대여소별 평균 절대 차 {sum(gap) / len(gap):.2f}대, 차이 0인 곳 {sum(g == 0 for g in gap)}곳")


if __name__ == "__main__":
    main()
