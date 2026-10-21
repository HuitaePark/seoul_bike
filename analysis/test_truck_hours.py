# Author: Shin Junyeop
# Date: 21OCT2026
# Purpose: 트럭 사건을 이동 시간으로 이어 최소 트럭 수를 세는 계산이 맞는지 가짜 사건으로 확인한다(파일 없이).

import unittest
from datetime import datetime, timedelta

from truck_hours import min_trucks

T0 = datetime(2026, 10, 21, 7)


def at(m):
    return T0 + timedelta(minutes=m)


class MinTrucksTest(unittest.TestCase):
    def test_reachable_events_share_a_truck_and_far_ones_do_not(self):
        # A(07:00–07:05)와 C(07:20–07:25)는 300m 떨어져 한 트럭이 잇는다. B(07:10–07:15)는 10km 떨어져 A·C 어느 쪽과도 못 잇는다
        events = [(at(0), at(5), 37.500, 127.000), (at(10), at(15), 37.590, 127.000), (at(20), at(25), 37.5027, 127.000)]
        chains = min_trucks(events)
        self.assertEqual(sorted(map(sorted, chains)), [[0, 2], [1]])

    def test_one_truck_covers_a_reachable_sequence(self):
        events = [(at(10 * k), at(10 * k + 5), 37.500 + 0.001 * k, 127.000) for k in range(5)]
        self.assertEqual(min_trucks(events), [[0, 1, 2, 3, 4]])

    def test_simultaneous_events_far_apart_need_separate_trucks(self):
        events = [(at(0), at(5), 37.50, 127.00), (at(0), at(5), 37.55, 127.05), (at(0), at(5), 37.60, 127.10)]
        self.assertEqual(len(min_trucks(events)), 3)


if __name__ == "__main__":
    unittest.main()
