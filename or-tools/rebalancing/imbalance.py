# Author: Jung Minkyu
# Date: 20OCT2026
# Purpose: 수요 예측을 '어느 대여소에서 몇 대를 수거/보충해야 하는지'로 바꾼다. (예측과 OR-Tools를 잇는 다리)

"""예측 결과를 솔버가 필요로 하는 '대여소별 수거/보충 수량'으로 바꾼다."""
from __future__ import annotations

from .forecast import DemandPredictor
from .models import Imbalance, Station


def healthy_band(station: Station, rentals: int, returns: int, safety: int) -> tuple[int, int]:
    """'지금 재고가 이 범위 안에 있으면 안전하다'는 범위 [lo, hi]를 구한다.

    안전하다 = 예측 구간이 끝났을 때 대여소가 비지도(대여 불가), 넘치지도(반납 불가) 않는다.

    구간이 끝난 시점의 재고:  end_stock = bikes - rentals + returns
    이것이 아래 두 조건을 만족해야 한다.
        (1) safety <= end_stock       -> 최소 safety대는 남아 있어야 한다 (비는 것 방지)
        (2) end_stock <= capacity     -> 용량을 넘으면 안 된다 (넘치는 것 방지)
    이 식을 'bikes'에 대해 풀면 지금 있어야 하는 재고의 범위가 나온다.
        (1) -> bikes >= rentals - returns + safety   (= lo, 하한)
        (2) -> bikes <= capacity + rentals - returns (= hi, 상한)

    예) 용량 20, 대여 5, 반납 13, safety 2 라면
        lo = 0 (5-13+2 가 음수라 0으로 보정), hi = 20+5-13 = 12  -> 지금 0~12대여야 안전.
        지금 18대면 hi(12)를 넘으므로 6대를 빼야(수거) 한다.
    """
    # 아래 min/max는 값이 말이 안 되는 경우(음수, 용량 초과, lo > hi)를 바로잡는 보정이다.
    lo = min(max(0, rentals - returns + safety), station.capacity)
    hi = max(min(station.capacity, station.capacity + rentals - returns), 0)
    return lo, max(lo, hi)


def compute_imbalances(
    stations: list[Station], predictor: DemandPredictor, hour: int, safety: int = 2
) -> list[Imbalance]:
    """모든 대여소를 검사해서 '불균형인 대여소'만 Imbalance 목록으로 돌려준다.

    safety: 구간이 끝났을 때도 남겨 두려는 최소 자전거 수(예측이 틀릴 때를 대비한 여유).
    """
    result = []
    for s in stations:
        f = predictor.predict(s, hour)  # 이 대여소의 예측 구간 동안의 예상 대여·반납 수
        lo, hi = healthy_band(s, f.rentals, f.returns, safety)
        if s.bikes > hi:
            result.append(Imbalance(s.id, s.bikes - hi))  # 남는다(+) -> 트럭이 수거(pickup)
        elif s.bikes < lo:
            result.append(Imbalance(s.id, s.bikes - lo))  # 모자란다(-) -> 트럭이 보충(dropoff)
        # lo <= bikes <= hi 이면 정상이라서 아무것도 만들지 않는다.
    return result
