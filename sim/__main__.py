# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: 구역 하나의 하루(또는 여러 날)를 정책마다 재생하고 지표를 표로 출력한다. 현행 운영 재현은 다음 날 0시 복원 재고와 맞는지도 본다.

"""실행: .venv/bin/python -m sim --terr 강남-05 --date 2025-06-11 [--until 2025-06-30] [--trucks 3]

지표는 둘 다 보고하고 판단은 실패 대여로 한다(2026-10-20 결정). 빈 대여소-분은 이용이 적은 대여소를 채워도 줄어든다.
실패 대여는 억눌린 수요 보정 전(기록된 대여만, 효과의 하한)과 보정 후(latent.py, 위쪽 추정)를 같이 낸다.
--until을 주면 날마다 돌려 data/derived/sim_<구역>_<시작>_<끝>.csv에 남기고 정책별 합계와 본 시스템이 이긴 날 수를 낸다.
"""
import argparse
import csv
from dataclasses import replace
from datetime import date, timedelta

from .engine import ROOT, load_day, replay
from .policies import Agent, CurrentOps, NoRebalancing, SimpleRule

# 본 시스템 설정: 2024 회수처 공급처, 근무조 안에서 매시 다시 계획, 늘 3시간 앞까지 본다
# (2025-06 8일 비교에서 고정 회수 9곳·근무조마다 1,298 → 1,248건, 다른 변형 1,306·1,322건)
AGENT_REPLAN_H, AGENT_HORIZON = 1, 3


MODEL_FALLBACK = "강남-05"  # 구역 모형이 없으면 옮겨 쓴다. 강북-02에서 빈 대여소 잡기 50.2%(자체 모형 51.6%), 구역 오차율 12.2%(11.8%)로 차이가 작았다


def policies_for(terr, d, day) -> dict:
    # 이름은 공개 API의 Policy 값과 같다(docs/specs/openapi.yaml)
    out = {"NONE": NoRebalancing(), "GREEDY": SimpleRule(day.fixed_pickup), "CURRENT_OPS": CurrentOps()}
    model = terr if (ROOT / f"data/models/nowcast_{terr}_rent.txt").exists() else MODEL_FALLBACK
    if (ROOT / f"data/models/nowcast_{model}_rent.txt").exists():  # 본 시스템은 학습된 예측기가 있을 때만(forecast/nowcast.py)
        from forecast.nowcast import load_predictor

        out["AGENT"] = Agent(load_predictor(terr, d, model), day.pickup_sources, replan_h=AGENT_REPLAN_H, horizon=AGENT_HORIZON)
    return out


def run_day(terr, d, trucks):
    """하루를 정책마다 보정 전·후로 재생한다. 반환: (day, rec, {정책: (보정 전 결과, 보정 후 결과)})"""
    day = load_day(terr, d)
    rec = replace(day, trips=day.trips[:day.n_recorded], n_recorded=None)  # 억눌린 수요 보정 전
    return day, rec, {name: (replay(rec, p, trucks=trucks), replay(day, p, trucks=trucks)) for name, p in policies_for(terr, d, day).items()}


def one_day(args) -> None:
    day, rec, res = run_day(args.terr, args.date, args.trucks)
    print(f"{args.terr} {args.date}  대여소 {len(day.stations)}곳  초기 재고 {sum(s.bikes for s in day.stations.values())}대  "
          f"대여 기록 {len(rec.trips)}건  억눌린 수요 {len(day.trips) - len(rec.trips)}건  역산 방문 {len(day.visits)}건  트럭 {args.trucks}대\n")
    print(f"{'정책':12} {'실패(보정 전)':>11} {'실패(보정 후)':>11} {'그중 억눌린':>9} {'빈 대여소-분':>12} {'과적재-분':>10} {'옮긴 대수':>8} {'시간당 대수':>10}")
    for name, (r0, r) in res.items():
        print(f"{name:12} {r0.failed:11,} {r.failed:11,} {r.failed_latent:9,} {r.empty_min:12,.0f} {r.over_min:10,.0f} {r.moved:8,} {r.moved_per_truck_h:10.1f}")

    # 검증: 현행 운영 재현으로 하루를 돌린 끝 재고는 다음 날 0시에 복원한 재고와 같아야 한다(같은 역산 기준을 쓰므로)
    nxt = load_day(args.terr, args.date + timedelta(days=1), latent=False).stations
    end = res["CURRENT_OPS"][0].stock
    common = end.keys() & nxt.keys()
    gap = [abs(end[s] - nxt[s].bikes) for s in common]
    print(f"\n검증(CURRENT_OPS 하루 끝 재고 vs 다음 날 0시 복원 재고, {len(common)}곳): "
          f"합계 {sum(end[s] for s in common):,} vs {sum(nxt[s].bikes for s in common):,}대, "
          f"대여소별 평균 절대 차 {sum(gap) / len(gap):.2f}대, 차이 0인 곳 {sum(g == 0 for g in gap)}곳")


def many_days(args) -> None:
    out = ROOT / f"data/derived/sim_{args.terr}_{args.date}_{args.until}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    d = args.date
    while d <= args.until:
        _, _, res = run_day(args.terr, d, args.trucks)
        for name, (r0, r) in res.items():
            rows.append(dict(date=d, region=args.terr, policy=name, failed_rentals=r0.failed, failed_rentals_adjusted=r.failed,
                             empty_station_minutes=round(r.empty_min), overflow_station_minutes=round(r.over_min),
                             bikes_moved=r.moved, bikes_moved_per_truck_hour=round(r.moved_per_truck_h, 1)))
        print(f"{d} " + "  ".join(f"{n} {r0.failed}/{r.failed}" for n, (r0, r) in res.items()), flush=True)
        d += timedelta(days=1)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    days = sorted({r["date"] for r in rows})
    by = {(r["date"], r["policy"]): r for r in rows}
    names = list(dict.fromkeys(r["policy"] for r in rows))
    print(f"\n{args.terr} {args.date} ~ {args.until} ({len(days)}일), 하루 평균")
    print(f"{'정책':12} {'실패(보정 전)':>11} {'실패(보정 후)':>11} {'빈 대여소-분':>12} {'옮긴 대수':>8}")
    for n in names:
        avg = lambda k: sum(by[(d, n)][k] for d in days) / len(days)  # noqa: E731
        print(f"{n:12} {avg('failed_rentals'):11,.0f} {avg('failed_rentals_adjusted'):11,.0f} {avg('empty_station_minutes'):12,.0f} {avg('bikes_moved'):8,.0f}")
    if "AGENT" in names:
        for other in (n for n in names if n != "AGENT"):
            win = sum(by[(d, "AGENT")]["failed_rentals_adjusted"] < by[(d, other)]["failed_rentals_adjusted"] for d in days)
            print(f"  AGENT가 {other}보다 실패(보정 후)가 적은 날: {win}/{len(days)}일")
    print(f"\n저장: {out.relative_to(ROOT)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--terr", default="강남-05")
    ap.add_argument("--date", type=date.fromisoformat, default=date(2025, 6, 11))
    ap.add_argument("--until", type=date.fromisoformat)
    ap.add_argument("--trucks", type=int)  # 주지 않으면 그 구역의 바쁜 날 동시 트럭 하한(ref_regions.csv, 역산 7절)
    args = ap.parse_args()
    if args.trucks is None:
        import duckdb

        args.trucks = duckdb.sql(f"SELECT trucks_min FROM '{ROOT}/analysis/out/ref_regions.csv' WHERE name = '{args.terr}'").fetchone()[0]
    many_days(args) if args.until else one_day(args)


if __name__ == "__main__":
    main()
