# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: 대여소가 실제로 비어 있던 시간에 빌리려던 사람(억눌린 수요)을 만들어 재생에 더한다.

"""억눌린 수요 (M4, PRD 7.3 재고 0 구간 보정).

대여 기록은 자전거가 있을 때만 생긴다. 그대로 재생하면 현행 운영 재현(CURRENT_OPS)은 구조상 실패가 적게 나오고,
실제로 비어 있던 시간을 채워 두는 정책은 점수를 받지 못한다. 그래서:
  1. 대여율 = 앞선 4주, 같은 요일 유형·같은 시각의 대여 수 ÷ 그 시각에 자전거가 있던 시간(재고 0이던 시간은 뺀다)
  2. 재생하는 날 실제로 재고가 0이던 구간마다 그 대여율로 포아송 도착을 만든다(난수 시드 고정)
  3. 목적지와 이용 시간은 그 대여소의 앞선 4주 실제 대여에서 뽑는다
재고는 역산 재고 복원(초기 재고와 같은 방식)으로 본다. 반납은 거치대에 막히지 않으므로 보정하지 않는다.
한계: 빈 대여소에서 못 빌린 사람 일부는 근처 대여소에서 빌렸고 그 대여는 이미 기록에 있다. 그래서 억눌린 수요는 위쪽 추정이다.
"""
from __future__ import annotations

import random
from collections import defaultdict
from datetime import date, datetime, timedelta

MIN_AVAIL_H = 1.0  # 자전거가 있던 시간이 이보다 적으면 대여율을 모르는 것으로 보고 만들지 않는다


def poisson_arrivals(s: datetime, e: datetime, rate_at, rng: random.Random) -> list[datetime]:
    """[s, e) 안의 포아송 도착 시각. rate_at(t)는 t가 속한 시각의 시간당 도착률. 시각이 바뀌면 그 시각의 도착률로 다시 뽑는다."""
    out, t = [], s
    while t < e:
        h_end = min(t.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1), e)
        rate = rate_at(t)
        while rate > 0:
            t = t + timedelta(hours=rng.expovariate(rate))
            if t >= h_end:
                break
            out.append(t)
        t = h_end
    return out


def empty_segments(c, reb, a: datetime, b: datetime, stations: set[int]) -> None:
    """[a, b) 동안 대여소별로 재고가 0이던 구간을 임시 표 z(st, s, e)로 만든다. 역산 재고 복원과 같은 방식이다:
    회수 창 중간 전에는 원래 대여소, 배치 창 중간 뒤에는 새 대여소에 있고, 정비 이동은 어디에도 세지 않는다.
    reb는 역산 결과 폴더(gaps_2024·2025, bike_pick·bike_drop). 두 해의 구간은 겹치지 않는다."""
    st_list = ",".join(map(str, stations))
    c.execute(f"""
    CREATE OR REPLACE TEMP TABLE iv AS
    WITH g AS (SELECT * FROM read_parquet(['{reb}/gaps_2024.parquet', '{reb}/gaps_2025.parquet']) WHERE t_to >= $a AND t_from < $b),
    p AS (SELECT bike_id, t_to, lo + (hi - lo) / 2 AS m FROM read_parquet(['{reb}/bike_pick_2024.parquet', '{reb}/bike_pick.parquet'])),
    d AS (SELECT bike_id, t_to, lo + (hi - lo) / 2 AS m FROM read_parquet(['{reb}/bike_drop_2024.parquet', '{reb}/bike_drop.parquet']))
    SELECT * FROM (
      SELECT from_st AS st, t_from AS s, t_to AS e FROM g WHERE NOT moved
      UNION ALL SELECT g.from_st, g.t_from, p.m FROM g JOIN p USING (bike_id, t_to)
      UNION ALL SELECT g.to_st, d.m, g.t_to FROM g JOIN d USING (bike_id, t_to))
    WHERE st IN ({st_list})""", {"a": a, "b": b})
    # 창이 시작할 때 비어 있던 대여소는 첫 사건 전까지도 0이다
    c.execute("""
    CREATE OR REPLACE TEMP TABLE z AS
    WITH ev AS (SELECT st, s AS ts, 1 AS dx FROM iv UNION ALL SELECT st, e, -1 FROM iv),
    ser AS (SELECT st, ts, sum(dx) OVER w AS stock, lead(ts) OVER w AS ts_next FROM ev
            WINDOW w AS (PARTITION BY st ORDER BY ts, dx ROWS UNBOUNDED PRECEDING))
    SELECT st, greatest(ts, $a) AS s, least(ts_next, $b) AS e FROM ser
    WHERE stock = 0 AND ts_next > $a AND ts < $b AND ts_next > ts
    UNION ALL
    SELECT st, $a, min(ts) FROM ev GROUP BY st HAVING min(ts) > $a""", {"a": a, "b": b})


def hourly_empty(c, until: datetime) -> None:
    """임시 표 z를 시각별로 나눠 zh(st, h, empty_min)를 만든다. until 전 시각만."""
    c.execute("""
    CREATE OR REPLACE TEMP TABLE zh AS
    SELECT st, h, sum(epoch(least(e, h + INTERVAL 1 HOUR) - greatest(s, h))) / 60 AS empty_min
    FROM (SELECT st, s, e, unnest(generate_series(date_trunc('hour', s), date_trunc('hour', e - INTERVAL 1 SECOND), INTERVAL 1 HOUR)) AS h
          FROM z WHERE s < $u) WHERE h < $u GROUP BY ALL""", {"u": until})


def latent_trips(c, reb, t0: datetime, holidays: set[date], stations: set[int], weeks: int = 4, seed: str = "") -> list[tuple]:
    """t0일 하루의 억눌린 대여를 (대여 시각, 대여소, 반납 시각, 반납소)로 돌려준다.
    c는 load_day의 DuckDB 연결(대여이력 뷰 tr이 있다). reb는 역산 결과 폴더(gaps·bike_pick·bike_drop)."""
    a, t1 = t0 - timedelta(weeks=weeks), t0 + timedelta(days=1)
    st_list = ",".join(map(str, stations))
    off = f"(dayofweek(h) IN (0, 6) OR h::DATE IN ({','.join(repr(str(x)) for x in holidays) or 'NULL'}))"
    empty_segments(c, reb, a, t1, stations)
    hourly_empty(c, t0)
    # 앞선 기간의 대여소·시각별 대여 수와 자전거가 있던 시간 → 요일 유형·시각별 대여율
    rates = {(st, o, hh): n / avail for st, o, hh, n, avail in c.execute(f"""
    WITH rn AS (SELECT rent_st AS st, date_trunc('hour', rent_dt) AS h, count(*) AS n FROM tr
           WHERE rent_dt >= $a AND rent_dt < $t0 AND rent_st IN ({st_list}) GROUP BY ALL),
    grid AS (SELECT unnest([{st_list}]) AS st, h FROM generate_series($a, $t0 - INTERVAL 1 HOUR, INTERVAL 1 HOUR) t(h))
    SELECT st, {off} AS o, hour(h) AS hh, sum(coalesce(rn.n, 0)) AS n, sum(60 - least(coalesce(zh.empty_min, 0), 60)) / 60 AS avail
    FROM grid LEFT JOIN rn USING (st, h) LEFT JOIN zh USING (st, h) GROUP BY ALL HAVING avail >= {MIN_AVAIL_H}""",
                                                                         {"a": a, "t0": t0}).fetchall()}
    # 목적지·이용 시간 후보: 그 대여소의 앞선 기간 실제 대여
    pool = defaultdict(list)
    # 순서를 고정해야 같은 시드로 같은 목적지를 뽑는다(DuckDB는 병렬로 읽어 순서가 바뀔 수 있다)
    for st, rtn_st, dur in c.execute(f"""SELECT rent_st, rtn_st, rtn_dt - rent_dt FROM tr
                                        WHERE rent_dt >= $a AND rent_dt < $t0 AND rent_st IN ({st_list})
                                        ORDER BY rent_st, rent_dt, rtn_st, rtn_dt""", {"a": a, "t0": t0}).fetchall():
        pool[st].append((rtn_st, dur))
    o = t0.weekday() >= 5 or t0.date() in holidays
    rng = random.Random(f"{t0:%Y-%m-%d}:{seed}")
    out = []
    for st, s, e in c.execute("SELECT st, s, e FROM z WHERE e > $t0 ORDER BY st, s", {"t0": t0}).fetchall():
        if not pool[st]:
            continue
        for t in poisson_arrivals(max(s, t0), min(e, t1), lambda t: rates.get((st, o, t.hour), 0.0), rng):
            rtn_st, dur = rng.choice(pool[st])
            out.append((t, st, t + dur, rtn_st))
    return out
