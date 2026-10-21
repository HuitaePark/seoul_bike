# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: 대여이력 재생 시뮬레이터. 정책(재배치 없음, 단순 규칙, ...)마다 같은 하루를 재생해 지표를 비교한다.

import sys
from pathlib import Path

# shortcut: or-tools/rebalancing이 설치 가능한 패키지가 되기 전까지 경로로 불러온다. 모노레포 구조로 옮기면 지운다
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "or-tools"))
