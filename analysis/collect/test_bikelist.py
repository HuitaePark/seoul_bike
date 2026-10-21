# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: bikeList 페이지 넘김이 1,000행 미만 페이지에서 멈추고 행을 빠짐없이 모으는지 확인한다(네트워크 없이).

import unittest

from bikelist import fetch_all


def fake_api(total):
    calls = []

    def get(a, b):
        calls.append((a, b))
        return [{"stationId": f"ST-{i}"} for i in range(a, min(b, total) + 1)]

    return get, calls


class FetchAllTest(unittest.TestCase):
    def test_stops_at_short_page(self):
        get, calls = fake_api(2716)
        self.assertEqual(len(fetch_all(get)), 2716)
        self.assertEqual(calls, [(1, 1000), (1001, 2000), (2001, 3000)])

    def test_exact_multiple_reads_one_empty_page(self):
        get, calls = fake_api(2000)
        self.assertEqual(len(fetch_all(get)), 2000)
        self.assertEqual(len(calls), 3)  # 세 번째 페이지는 INFO-200(빈 목록)


if __name__ == "__main__":
    unittest.main()
