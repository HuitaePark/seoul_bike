# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: 손으로 적은 행사 목록(행사장, 날짜, 시각)으로 도착·퇴장 시간대와 행사장 반경 700m 대여소를 표로 낸다. 예측기의 행사 피처 입력.

"""행사 달력 (예측 개선 작업 5, PRD FR-09).

입력 (손으로 적는다. 한 줄 = 행사 하루):
  analysis/in/events.csv  date(YYYY-MM-DD), name, venue(venues.csv의 venue), kind(KINDS 중 하나),
                          start·end(HH:MM, 그 행사장에서 시작·끝. 비우면 아래 기본값), closed(1 = 그날 그 일대 통제), source(출처 URL, 선택),
                          attendance(관객 수, 선택. 예측기의 ev_att)
  analysis/in/venues.csv  venue, place, lat, lon. 새 행사장은 여기에 한 줄 더한다
기본값: start를 비우면 평일 19시·토 18시·일 17시, end를 비우면 start + DUR[kind]시간. closed = 1이고 시각이 없으면 하루 종일 퇴장으로 본다.
도착은 시작 4시간 전부터 시작까지, 퇴장은 끝부터 2시간. 출발·도착점인 행사(자전거대행진 도착 등)는 start = end = 그 시각으로 적는다.
출력: analysis/out/events.csv (type, name, venue, date, arrive_h0, arrive_h1, exit_h0, exit_h1, closed, attendance; type = KINDS 순번 1..),
      analysis/out/event_stations.csv (venue, st_no, dist_m)
실행: .venv/bin/python analysis/events.py
"""
import csv
from datetime import date
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KINDS = ("축구", "야구", "콘서트", "체육행사", "축제", "집회", "기타", "농구", "배구")  # 순번이 피처 값이라 새 종류는 끝에 붙인다
DUR = {"축구": 2, "야구": 3, "콘서트": 3, "농구": 2, "배구": 2}  # 끝 시각을 비웠을 때 공연·경기 시간. 나머지 종류는 3시간
RADIUS_M = 700


def hour(s: str) -> int:
    h, m = s.split(":")
    assert 0 <= int(h) <= 24 and 0 <= int(m) < 60, s
    return int(h)


def windows(r: dict) -> tuple:
    """(도착 시작, 도착 끝, 퇴장 시작, 퇴장 끝) 시. 0~24로 자른다."""
    if r["closed"] == "1" and not r["start"] and not r["end"]:
        return 0, 0, 0, 24
    dow = date.fromisoformat(r["date"]).weekday()
    s = hour(r["start"]) if r["start"] else (17 if dow == 6 else 18 if dow == 5 else 19)
    e = hour(r["end"]) if r["end"] else s + DUR.get(r["kind"], 3)
    return max(s - 4, 0), s, min(e, 24), min(e + 2, 24)


def read_events() -> list[tuple]:
    venues = {r["venue"]: (float(r["lat"]), float(r["lon"])) for r in csv.DictReader(open(ROOT / "analysis/in/venues.csv"))}
    rows, bad = [], []
    for i, r in enumerate(csv.DictReader(open(ROOT / "analysis/in/events.csv")), start=2):
        try:
            date.fromisoformat(r["date"])
            assert r["venue"] in venues, f"venues.csv에 없는 행사장 {r['venue']!r}"
            assert r["kind"] in KINDS, f"kind는 {KINDS} 중 하나 ({r['kind']!r})"
            assert r["closed"] in ("0", "1", ""), f"closed는 0 또는 1 ({r['closed']!r})"
            assert r.get("attendance", "") in ("", None) or r["attendance"].isdigit(), f"attendance는 숫자 ({r['attendance']!r})"
            r["closed"] = r["closed"] or "0"
            rows.append((KINDS.index(r["kind"]) + 1, r["name"], r["venue"], r["date"], *windows(r), int(r["closed"]), r.get("attendance") or ""))
        except (AssertionError, ValueError) as e:
            bad.append(f"  {i}행 {r.get('date')} {r.get('name')}: {e}")
    if bad:  # 손으로 적은 표라 틀린 줄을 한 번에 모두 알려 준다
        raise SystemExit("analysis/in/events.csv에 고칠 줄이 있다:\n" + "\n".join(bad))
    return sorted(rows, key=lambda x: (x[3], x[2])), venues


def dist_m(a, b):
    dlat, dlon = radians(b[0] - a[0]), radians(b[1] - a[1])
    h = sin(dlat / 2) ** 2 + cos(radians(a[0])) * cos(radians(b[0])) * sin(dlon / 2) ** 2
    return 2 * 6_371_000 * asin(sqrt(h))


def main() -> None:
    events, venues = read_events()
    out = ROOT / "analysis/out"
    with open(out / "events.csv", "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["type", "name", "venue", "date", "arrive_h0", "arrive_h1", "exit_h0", "exit_h1", "closed", "attendance"])
        w.writerows(events)
    used = {e[2] for e in events}
    near = []
    with open(out / "stations.csv") as f:
        for r in csv.DictReader(f):
            if r["lat"] and r["lon"]:
                for v in sorted(used):
                    d = dist_m(venues[v], (float(r["lat"]), float(r["lon"])))
                    if d <= RADIUS_M:
                        near.append((v, r["st_no"], round(d)))
    with open(out / "event_stations.csv", "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["venue", "st_no", "dist_m"])
        w.writerows(near)
    print(f"행사일 {len(events)}개, 행사장 반경 {RADIUS_M}m 대여소 {len(near)}곳 "
          + str({v: sum(1 for x in near if x[0] == v) for v in sorted(used)}))


if __name__ == "__main__":
    main()
