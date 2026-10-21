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

from .engine import ROOT, load_day, replay, resample_demand
from .policies import Agent, CurrentOps, CurrentOpsLastWeek, NoRebalancing, SimpleRule

# 본 시스템 설정: 2024 회수처 공급처, 근무조 안에서 매시 다시 계획, 늘 3시간 앞까지 보고, 공급처에서 뺄 때는 여유를 2배로
# (2025-06 강남-05 8일: 고정 회수 9곳·근무조마다 1,298 → 매시·3시간 1,248 → 회수 여유 2배 1,230건. 보정 전 421 → 366건)
# 경로 탐색은 5의 plan_routes 기본값(해 50개에서 멈춤)이라 실행마다 같은 답이 나온다. 예전 2초 제한은 같은 문제를 다시 풀면 16개 중 8개만 같았다
# 근무조 안에서 트럭 위치·적재량을 다음 계획에 넘기고(carry_trucks), 최적화기에 엔진과 같은 이동 시간을 준다(sim_travel).
# 서울 1주일(2025-06-09~15) 다시 뽑기: 17,911 → 17,249건(현행 운영 재현 18,092), 기록 재생: 12,343 → 11,614건(11,477)
# 포기 벌점을 실패 기대값으로 둔다(fail_priority: 배치는 막는 실패 대여 기대값, 회수 1대 = 실패 0.25건). 회수 0.5·1.0과 벌점 규모 ×1/3·×10/3은 덜 줄었다.
# 공급이 배치 요청의 30%도 안 되는 계획에서는 공급처 밖의 확실한 넘침(거치대 + 5대 초과)에서도 뺀다(pick_margin, pick_starve).
# 4구역 × 3일 묶음 셋(기록 / 다시 뽑기 2번 합): main 13,828/40,400 → 13,436/39,548건, holdout 6,516/21,022 → 6,365/20,222건,
# 실험에 쓰지 않은 묶음(강북-01·강남-02·강북-06·강남-04 × 06-17·19·21) 9,051/26,153 → 8,726/25,793건. 공급 몫은 공급이 모자란 구역(강북-03·강남-06)에서만 난다
AGENT_KW = dict(replan_h=1, horizon=3, z_pick=2, carry_trucks=True, sim_travel=True, fail_priority=0.25, pick_margin=5, pick_starve=0.3)


MODEL_FALLBACK = "강남-05"  # 구역 모형이 없으면 옮겨 쓴다. 강북-02에서 빈 대여소 잡기 50.2%(자체 모형 51.6%), 구역 오차율 12.2%(11.8%)로 차이가 작았다


def policies_for(terr, d, day) -> dict:
    # 이름은 공개 API의 Policy 값과 같다(docs/specs/openapi.yaml)
    # CURRENT_OPS_LW(지난주 같은 요일의 방문으로 운영)와 CURRENT_OPS_CAP(현행 운영 재현을 본 시스템과 같은 공급으로)은 API에 없는 분석용 정책이다
    out = {"NONE": NoRebalancing(), "GREEDY": SimpleRule(day.fixed_pickup), "CURRENT_OPS": CurrentOps(),
           "CURRENT_OPS_CAP": CurrentOps(same_supply=True), "CURRENT_OPS_LW": CurrentOpsLastWeek()}
    model = terr if (ROOT / f"data/models/nowcast_{terr}_rent.txt").exists() else MODEL_FALLBACK
    if (ROOT / f"data/models/nowcast_{model}_rent.txt").exists():  # 본 시스템은 학습된 예측기가 있을 때만(forecast/nowcast.py)
        from forecast.nowcast import load_predictor

        out["AGENT"] = Agent(load_predictor(terr, d, model), day.pickup_sources, **AGENT_KW)
    return out


SOURCES_WEEKS = None  # sim --sources-weeks: 본 시스템 공급처를 그날 전 몇 주의 역산 방문으로 고른다(기본은 전해 4~10월)


def run_day(terr, d, trucks, resample=0):
    """하루를 정책마다 보정 전·후로 재생한다. resample > 0이면 수요를 그만큼 다시 뽑은 하루(보정 후)도 재생한다.
    반환: (day, rec, {정책: (보정 전 결과, 보정 후 결과)}, [{정책: 다시 뽑은 하루의 결과}, ...])"""
    day = load_day(terr, d, sources_weeks=SOURCES_WEEKS)
    rec = replace(day, trips=day.trips[:day.n_recorded], n_recorded=None)  # 억눌린 수요 보정 전
    pols = policies_for(terr, d, day)
    res = {name: (replay(rec, p, trucks=trucks), replay(day, p, trucks=trucks)) for name, p in pols.items()}
    worlds = [resample_demand(day, seed=k) for k in range(resample)]
    return day, rec, res, [{name: replay(w, p, trucks=trucks) for name, p in pols.items()} for w in worlds]


def one_day(args) -> None:
    day, rec, res, _ = run_day(args.terr, args.date, args.trucks)
    print(f"{args.terr} {args.date}  대여소 {len(day.stations)}곳  초기 재고 {sum(s.bikes for s in day.stations.values())}대  "
          f"대여 기록 {len(rec.trips)}건  억눌린 수요 {len(day.trips) - len(rec.trips)}건  역산 방문 {len(day.visits)}건  트럭 {args.trucks}대\n")
    print(f"{'정책':12} {'실패(보정 전)':>11} {'실패(보정 후)':>11} {'그중 억눌린':>9} {'빈 대여소-분':>12} {'과적재-분':>10} {'옮긴 대수':>8} {'시간당 대수':>10}")
    for name, (r0, r) in res.items():
        print(f"{name:12} {r0.failed:11,} {r.failed:11,} {r.failed_latent:9,} {r.empty_min:12,.0f} {r.over_min:10,.0f} {r.moved:8,} {r.moved_per_truck_h:10.1f}")
    if day.closures:
        print(f"\n일시 운영 중단 {len(day.closures)}곳: 닫혀 못 한 대여(보정 후, 실패와 따로) " + ", ".join(f"{n} {r.closed_rentals:,}" for n, (_, r) in res.items())
              + " / 옮긴 반납 " + ", ".join(f"{n} {r.diverted_returns:,}" for n, (_, r) in res.items()))

    # 검증: 현행 운영 재현으로 하루를 돌린 끝 재고는 다음 날 0시에 복원한 재고와 같아야 한다(같은 역산 기준을 쓰므로)
    nxt = load_day(args.terr, args.date + timedelta(days=1), latent=False).stations
    end = res["CURRENT_OPS"][0].stock
    common = end.keys() & nxt.keys()
    gap = [abs(end[s] - nxt[s].bikes) for s in common]
    print(f"\n검증(CURRENT_OPS 하루 끝 재고 vs 다음 날 0시 복원 재고, {len(common)}곳): "
          f"합계 {sum(end[s] for s in common):,} vs {sum(nxt[s].bikes for s in common):,}대, "
          f"대여소별 평균 절대 차 {sum(gap) / len(gap):.2f}대, 차이 0인 곳 {sum(g == 0 for g in gap)}곳")


def many_days(args) -> None:
    tag = "" if args.shifts is None else "_s" + "_".join(f"{a}-{b}" for a, b in args.shifts)
    tag += f"_src{args.sources_weeks}w" if args.sources_weeks else ""
    tag += "_nocarry" if args.no_carry_trucks else ""
    tag += "_nosimtt" if args.no_sim_travel else ""
    out = ROOT / f"data/derived/sim_{args.terr}_{args.date}_{args.until}{tag}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    d = args.date
    while d <= args.until:
        _, _, res, worlds = run_day(args.terr, d, args.trucks, args.resample)
        for name, (r0, r) in res.items():
            rows.append(dict(date=d, region=args.terr, world="recorded", policy=name, failed_rentals=r0.failed, failed_rentals_adjusted=r.failed,
                             empty_station_minutes=round(r.empty_min), overflow_station_minutes=round(r.over_min),
                             bikes_moved=r.moved, bikes_moved_per_truck_hour=round(r.moved_per_truck_h, 1),
                             bikes_dropped=r.dropped, closed_rentals=r.closed_rentals, diverted_returns=r.diverted_returns))
        for k, w in enumerate(worlds):  # 다시 뽑은 하루: 억눌린 수요 포함 값만 낸다(failed_rentals는 그중 다시 뽑은 대여의 실패)
            for name, r in w.items():
                rows.append(dict(date=d, region=args.terr, world=f"resample{k}", policy=name, failed_rentals=r.failed - r.failed_latent,
                                 failed_rentals_adjusted=r.failed, empty_station_minutes=round(r.empty_min),
                                 overflow_station_minutes=round(r.over_min), bikes_moved=r.moved,
                                 bikes_moved_per_truck_hour=round(r.moved_per_truck_h, 1),
                                 bikes_dropped=r.dropped, closed_rentals=r.closed_rentals, diverted_returns=r.diverted_returns))
        print(f"{d} " + "  ".join(f"{n} {r0.failed}/{r.failed}" for n, (r0, r) in res.items()), flush=True)
        d += timedelta(days=1)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    days = sorted({r["date"] for r in rows})
    by = {(r["date"], r["policy"]): r for r in rows if r["world"] == "recorded"}
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
    if args.resample:
        print(f"\n수요를 다시 뽑은 하루 {args.resample}번 평균 (실패, 억눌린 수요 포함)")
        for n in names:
            v = [r["failed_rentals_adjusted"] for r in rows if r["policy"] == n and r["world"] != "recorded"]
            print(f"  {n:14} {sum(v) / len(days) / args.resample:11,.0f}")
    print(f"\n저장: {out.relative_to(ROOT)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--terr", default="강남-05")
    ap.add_argument("--date", type=date.fromisoformat, default=date(2025, 6, 11))
    ap.add_argument("--until", type=date.fromisoformat)
    ap.add_argument("--resample", type=int, default=0)  # --until과 같이: 수요를 다시 뽑은 하루를 날마다 몇 번 더 재생할지
    ap.add_argument("--trucks", type=int)  # 주지 않으면 그 구역의 바쁜 날 동시 트럭 하한(ref_regions.csv, 역산 7절)
    ap.add_argument("--shifts", type=lambda s: tuple(tuple(map(int, x.split("-"))) for x in s.split(",")),
                    help="본 시스템의 근무 시간. 예: 7-22 또는 7-10,13-16,19-21(기본, PRD 7.4)")
    ap.add_argument("--no-carry-trucks", action="store_true", help="본 시스템: 다시 계획할 때마다 거점에서 출발(예전 방식)")
    ap.add_argument("--no-sim-travel", action="store_true", help="본 시스템: 최적화기 기본 이동 시간(직선, 시속 30km)을 쓴다")
    ap.add_argument("--sources-weeks", type=int, help="본 시스템 공급처를 그날 전 이 주 수만큼의 역산 방문으로 고른다(기본은 전해 4~10월)")
    args = ap.parse_args()
    if args.shifts:
        assert all(0 <= a < b <= 24 for a, b in args.shifts), args.shifts
        AGENT_KW["shifts"] = args.shifts
    global SOURCES_WEEKS
    SOURCES_WEEKS = args.sources_weeks
    AGENT_KW.update(carry_trucks=not args.no_carry_trucks, sim_travel=not args.no_sim_travel)
    if args.trucks is None:
        import duckdb

        args.trucks = duckdb.sql(f"SELECT trucks_min FROM '{ROOT}/analysis/out/ref_regions.csv' WHERE name = '{args.terr}'").fetchone()[0]
    many_days(args) if args.until else one_day(args)


if __name__ == "__main__":
    main()
