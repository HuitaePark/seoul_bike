# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: 수요 예측기(DemandPredictor 구현체). 베이스라인 → LightGBM 순서로 더한다.

import sys
from pathlib import Path

# shortcut: or-tools/rebalancing이 설치 가능한 패키지가 되기 전까지 경로로 불러온다. 모노레포 구조로 옮기면 지운다
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "or-tools"))
