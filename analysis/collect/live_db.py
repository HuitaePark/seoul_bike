# Author: 4 백엔드 (bikelist.py의 수집 함수를 그대로 쓴다)
# Date: 21OCT2026
# Purpose: 따릉이 실시간 재고를 업무 DB 표 live.station_status에 5분마다 넣는다 (합의 12). 서버 compose 서비스 collector가 돌린다.

"""실시간 재고 → 업무 DB (live.station_status).

왜: backend는 재고를 직접 수집하지 않고 이 표에서 대여소별 최신 행만 읽는다 (합의 12).
    대여소 ID는 API가 준 stationId(ST-숫자)를 그대로 쓴다. 대여소번호로 바꾸지 않는다 (ADR 0001).
입력: SEOUL_API_KEY (비어 있으면 수집하지 않고 기다린다. 헬스체크는 통과해 배포를 막지 않는다),
      PGHOST·PGUSER·PGPASSWORD·PGDATABASE (libpq 기본 변수), COLLECTOR_INTERVAL_SECONDS(기본 300),
      COLLECTOR_RETENTION_DAYS(기본 14), COLLECTOR_HEARTBEAT(기본 /tmp/collector-alive, 헬스체크가 본다)
출력: live.station_status (observed_at, station_id, bikes, docks). 같은 시각·대여소는 한 번만 들어간다.
      하루 한 번 보관 기간보다 오래된 행을 지운다 (2,716곳 × 5분이면 하루 약 78만 행).
실행: python analysis/collect/live_db.py
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
import signal
import threading
from pathlib import Path

import bikelist

log = logging.getLogger("collector")
STATION_ID = re.compile(r"^ST-\d+$")
INSERT = ("insert into live.station_status (observed_at, station_id, bikes, docks) values (%s, %s, %s, %s) "
          "on conflict do nothing")
PURGE_BATCH = 50_000  # 한 번에 지우는 행 수. 크게 지우면 표 잠금이 길어진다


def to_rows(raw, observed_at):
    """API 행을 표의 행으로 바꾼다. 대여소 ID가 ST-숫자가 아니거나 대수가 음수·숫자가 아니면 뺀다. 반환: (행 목록, 뺀 수)"""
    rows, skipped = [], 0
    for r in raw:
        sid = str(r.get("stationId", "")).strip()
        try:
            bikes, docks = int(r["parkingBikeTotCnt"]), int(r["rackTotCnt"])
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        if not STATION_ID.match(sid) or bikes < 0 or docks < 0:
            skipped += 1
            continue
        rows.append((observed_at, sid, bikes, docks))
    return rows, skipped


def next_slot(now, interval):
    """now 다음의 interval초 경계 (5분이면 :00, :05, ...). 모든 대여소를 같은 시각으로 맞춰 둔다."""
    return dt.datetime.fromtimestamp((int(now.timestamp()) // interval + 1) * interval, now.tzinfo)


def mask(text, key):
    """오류 문장에 키가 섞여 로그에 남지 않게 한다."""
    return text.replace(key, "***") if key else text


def collect_once(conn, fetch, now):
    """한 번 받아 한 트랜잭션으로 넣는다. 반환: (넣으려 한 행 수, 뺀 행 수)"""
    rows, skipped = to_rows(fetch(), now.replace(second=0, microsecond=0))
    with conn.cursor() as cur:
        cur.executemany(INSERT, rows)
    conn.commit()
    return len(rows), skipped


def purge(conn, before):
    """before보다 오래된 행을 나눠 지운다. 반환: 지운 행 수"""
    total = 0
    while True:
        with conn.cursor() as cur:
            cur.execute("delete from live.station_status where ctid in (select ctid from live.station_status "
                        "where observed_at < %s limit %s)", (before, PURGE_BATCH))
            n = cur.rowcount
        conn.commit()
        total += n
        if n < PURGE_BATCH:
            return total


def run(stop, *, key, interval, retention_days, heartbeat, connect, fetch,
        now=lambda: dt.datetime.now(bikelist.KST)):
    """stop이 설정될 때까지 interval초 경계마다 수집한다. 키가 없으면 수집하지 않고 heartbeat만 갱신한다.

    API·DB 오류는 로그만 남기고 다음 주기에 다시 시도한다. 매 주기 heartbeat를 갱신하므로 헬스체크는
    "프로세스가 돌고 있다"만 뜻한다. 수집이 실제로 되는지는 로그와 표의 최신 observed_at으로 본다.
    """
    heartbeat.touch()
    if not key:
        log.warning("SEOUL_API_KEY가 비어 있어 수집하지 않는다. GitHub production 환경 secret SEOUL_API_KEY를 넣고 "
                    "deploy를 다시 돌린다 (infra/README.md \"collector\")")
        while not stop.wait(interval):
            heartbeat.touch()
        return
    last_purge = None
    while True:
        t = now()
        if stop.wait((next_slot(t, interval) - t).total_seconds()):
            return
        heartbeat.touch()
        t = now()
        try:
            with connect() as conn:
                n, skipped = collect_once(conn, fetch, t)
                log.info("%s 대여소 %d곳 저장 (뺀 행 %d)", f"{t:%Y-%m-%d %H:%M}", n, skipped)
                if last_purge != t.date():
                    gone = purge(conn, t - dt.timedelta(days=retention_days))
                    last_purge = t.date()
                    log.info("%d일보다 오래된 행 %d개를 지웠다", retention_days, gone)
        except Exception as e:  # noqa: BLE001 (주기마다 다시 시도한다)
            log.error("수집 실패: %s", mask(f"{type(e).__name__}: {e}", key))


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    import psycopg  # 서버 이미지에만 있다. 노트북 cron과 테스트에는 필요 없다

    key = os.environ.get("SEOUL_API_KEY", "").strip()
    stop = threading.Event()
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, lambda *_: stop.set())
    # 공개 키 sample은 한 번에 5행까지만 준다(그보다 크게 부르면 오류 응답). 시험용이라 1~5행만 받는다.
    if key == "sample":
        fetch = lambda: bikelist.get_page(key, 1, 5)  # noqa: E731
    else:
        fetch = lambda: bikelist.fetch_all(lambda a, b: bikelist.get_page(key, a, b))  # noqa: E731
    run(stop, key=key,
        interval=int(os.environ.get("COLLECTOR_INTERVAL_SECONDS", "300")),
        retention_days=int(os.environ.get("COLLECTOR_RETENTION_DAYS", "14")),
        heartbeat=Path(os.environ.get("COLLECTOR_HEARTBEAT", "/tmp/collector-alive")),
        connect=lambda: psycopg.connect("", connect_timeout=10),
        fetch=fetch)


if __name__ == "__main__":
    main()
