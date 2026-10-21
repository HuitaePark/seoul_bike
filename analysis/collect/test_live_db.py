# Author: 4 백엔드
# Date: 21OCT2026
# Purpose: 실시간 재고를 DB에 넣는 collector가 행을 바르게 바꾸고, 키가 없으면 수집하지 않고, 오류에도 계속 도는지 확인한다(네트워크·DB 없이).

import datetime as dt
import tempfile
import unittest
from pathlib import Path

import live_db

KST = dt.timezone(dt.timedelta(hours=9))
T = dt.datetime(2026, 10, 21, 10, 2, 30, 400000, tzinfo=KST)


class Stop:
    """처음 n번은 기다린 뒤 계속하고 그다음부터 멈추라고 답한다."""

    def __init__(self, rounds):
        self.rounds, self.waits = rounds, []

    def wait(self, seconds):
        self.waits.append(seconds)
        self.rounds -= 1
        return self.rounds < 0


class Cursor:
    def __init__(self, conn):
        self.conn, self.rowcount = conn, 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def executemany(self, sql, rows):
        self.conn.inserted += list(rows)

    def execute(self, sql, params):
        self.conn.purged_before.append(params[0])
        self.rowcount = 0


class Conn:
    def __init__(self):
        self.inserted, self.purged_before, self.commits = [], [], 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return Cursor(self)

    def commit(self):
        self.commits += 1


def row(sid, bikes, docks):
    return {"stationId": sid, "parkingBikeTotCnt": bikes, "rackTotCnt": docks, "stationName": "102. 망원역 1번출구 앞"}


class LiveDbTest(unittest.TestCase):
    def setUp(self):
        self.heartbeat = Path(tempfile.mkdtemp()) / "alive"

    def test_rows_keep_the_api_station_id_and_drop_bad_ones(self):
        # 대여소 ID는 API의 ST-숫자 그대로다. 이름 앞의 대여소번호(102)로 바꾸지 않는다 (ADR 0001)
        rows, skipped = live_db.to_rows(
            [row("ST-4", "12", "15"), row("102", "3", "10"), row("ST-x", "1", "5"), row("ST-5", "다수", "14"),
             row("ST-6", "-1", "13"), {"stationId": "ST-7"}], T)
        self.assertEqual(rows, [(T, "ST-4", 12, 15)])
        self.assertEqual(skipped, 5)

    def test_collection_times_line_up_on_five_minute_boundaries(self):
        self.assertEqual(live_db.next_slot(T, 300), dt.datetime(2026, 10, 21, 10, 5, tzinfo=KST))
        on_boundary = dt.datetime(2026, 10, 21, 10, 5, tzinfo=KST)
        self.assertEqual(live_db.next_slot(on_boundary, 300), dt.datetime(2026, 10, 21, 10, 10, tzinfo=KST))

    def test_without_a_key_it_waits_without_calling_the_api_or_the_db(self):
        calls = []
        live_db.run(Stop(2), key="", interval=300, retention_days=14, heartbeat=self.heartbeat,
                    connect=lambda: calls.append("db"), fetch=lambda: calls.append("api"))
        self.assertEqual(calls, [])
        self.assertTrue(self.heartbeat.exists())  # 헬스체크가 보는 파일이 있어 배포를 막지 않는다

    def test_one_round_stores_rows_at_the_minute_and_purges_old_rows_once(self):
        conn = Conn()
        live_db.run(Stop(1), key="k", interval=300, retention_days=14, heartbeat=self.heartbeat,
                    connect=lambda: conn, fetch=lambda: [row("ST-4", 12, 15), row("ST-5", 6, 14)], now=lambda: T)
        minute = T.replace(second=0, microsecond=0)
        self.assertEqual(conn.inserted, [(minute, "ST-4", 12, 15), (minute, "ST-5", 6, 14)])
        self.assertEqual(conn.purged_before, [T - dt.timedelta(days=14)])
        self.assertGreaterEqual(conn.commits, 2)

    def test_a_failed_round_is_logged_without_the_key_and_the_loop_goes_on(self):
        def broken():
            raise RuntimeError("http://openapi.seoul.go.kr:8088/secret-key/json/bikeList/1/1000/ 500")

        stop = Stop(2)
        with self.assertLogs("collector", level="ERROR") as logs:
            live_db.run(stop, key="secret-key", interval=300, retention_days=14, heartbeat=self.heartbeat,
                        connect=Conn, fetch=broken, now=lambda: T)
        self.assertEqual(len(stop.waits), 3)  # 두 번 실패해도 멈추지 않고 다음 주기를 기다렸다
        self.assertTrue(all("secret-key" not in line and "***" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
