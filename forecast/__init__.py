# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: 수요 예측기(DemandPredictor 구현체). 베이스라인 → LightGBM 순서로 더한다.

import sys
from pathlib import Path

# shortcut: or-tools/rebalancing이 설치 가능한 패키지가 되기 전까지 경로로 불러온다. 모노레포 구조로 옮기면 지운다
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "or-tools"))

from dataclasses import dataclass  # noqa: E402
from datetime import datetime  # noqa: E402


@dataclass(frozen=True)
class Forecast:
    """한 대여소의 예측 구간(start부터 horizon_h시간) 동안의 예상 수요. 값은 1시간 단위로 나눠 담는다.
    rentals_h[i], returns_h[i]는 start + i시간부터 1시간 동안의 값이다(길이 = horizon_h). 기댓값이라 실수다.
    시간별로 담는 이유: 구간 합계만 보면 "아침에 비었다가 저녁에 다시 차는" 대여소를 정상으로 본다(PRD 7.1 궤적).
    optimizer(or-tools)의 Forecast는 아직 구간 합계 하나다. 시간별로 바꾸는 제안은 따로 올렸다(docs/data/predict.md)."""

    start: datetime  # 예측 구간 시작 시각(정시)
    rentals_h: tuple[float, ...]  # 시간별로 대여되어 대여소에서 나갈 것으로 예상되는 자전거 수
    returns_h: tuple[float, ...]  # 시간별로 반납되어 대여소로 들어올 것으로 예상되는 자전거 수
    model_version: str  # 어떤 예측기·버전이 만든 값인지(API의 modelVersion)

    @property
    def rentals(self) -> float:
        return sum(self.rentals_h)

    @property
    def returns(self) -> float:
        return sum(self.returns_h)
