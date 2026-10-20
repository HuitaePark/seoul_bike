# 수요 예측 인터페이스 `predict()` 초안

| 항목 | 내용 |
| --- | --- |
| 문서 상태 | 초안 v0.1 (5 최적화 담당과 M1에 합의) |
| 작성일 | 2026-10-20 |
| 대상 코드 | [forecast.py](../../or-tools/rebalancing/forecast.py), [imbalance.py](../../or-tools/rebalancing/imbalance.py), [models.py](../../or-tools/rebalancing/models.py) |

**제안:** 시그니처를 `predict(station, start, horizon_h)`로 바꾸고, `Forecast`가 **시간별** 대여·반납을 담게 한다. 기존 `rentals`·`returns`는 합계 속성으로 남겨 `healthy_band`는 그대로 돈다.

---

## 1. 지금 코드가 PRD와 맞지 않는 점

| 지금 | PRD가 요구하는 것 |
| --- | --- |
| `predict(station, hour: int)`. 날짜가 없다 | 요일, 공휴일, 날씨, 행사를 피처로 쓴다 (7.3). 날짜가 있어야 한다 |
| 1시간 합계 하나 (`rentals`, `returns`, 정수) | 3시간 동안의 **예상 재고 궤적**이 범위를 벗어나지 않게 한다 (7.1). 구간 끝값만으로는 중간에 비는 대여소를 놓친다 |
| `HeuristicPredictor`(규칙 기반 가짜) | 베이스라인 → LightGBM으로 교체 (7.3) |

## 2. 선택지

| 안 | 내용 | 좋은 점 | 아쉬운 점 |
| --- | --- | --- | --- |
| A | 시그니처만 바꾸고 구간 합계만 돌려줌 | 5의 코드 변경이 가장 적다 | 궤적을 못 본다. 아침에 비었다가 저녁에 차는 대여소를 정상으로 판정한다 |
| **B (추천)** | 시간별 값을 돌려주고 합계는 속성으로 둠 | PRD 7.1 궤적 검사가 가능하다. 기존 `healthy_band`는 합계로 그대로 돈다 | `Forecast` 모양이 바뀌어 5의 테스트를 같이 고쳐야 한다 |

## 3. 안 B

```python
@dataclass(frozen=True)
class Forecast:
    start: datetime                 # 예측 구간 시작 (KST, 정시)
    rentals_h: tuple[float, ...]    # 시간별 예상 대여 (대), 길이 = horizon_h
    returns_h: tuple[float, ...]    # 시간별 예상 반납 (대)
    model_version: str              # "baseline-4w" 등. API Forecast의 modelVersion. 거점별 근거(FR-03)에 표시

    @property
    def rentals(self) -> float:     # 구간 합계. 기존 healthy_band가 쓰는 값
        return sum(self.rentals_h)

    @property
    def returns(self) -> float:
        return sum(self.returns_h)


class DemandPredictor(Protocol):
    def predict(self, station: Station, start: datetime, horizon_h: int) -> Forecast: ...
```

| 정할 것 | 제안 |
| --- | --- |
| 값의 형식 | 기댓값이라 실수. 정수 대수가 필요한 곳(필요 대수)에서 반올림한다 |
| `Station.id` | 대여이력의 `st_no` ([데이터 명세](data-spec.md)) |
| 날씨·공휴일 | 예측기가 `start`로 직접 찾는다. 시그니처에 넣지 않는다 |
| 대여·반납 중지(`station_suspended`) | 예측기가 아니라 호출하는 쪽(`compute_targets`)이 수요를 0으로 바꾼다. 예측기는 제약을 모른다 |
| 시간 | 노드 안에서 `datetime.now()`를 부르지 않고 `start`를 받는다. 시뮬레이터가 가상 시각을 넣는다 (NFR-04) |

## 4. 바뀌는 곳과 담당

| 파일 | 변경 | 담당 |
| --- | --- | --- |
| `models.py` | `Forecast`를 위 모양으로 | 6이 초안 작성 (2026-10-20), 5가 PR에서 확인 |
| `forecast.py` | `DemandPredictor` 시그니처, `HeuristicPredictor`를 시간별 값으로 | 6이 초안 작성 (2026-10-20), 5가 PR에서 확인 |
| `imbalance.py` | `compute_imbalances(stations, predictor, start, horizon_h=1)`. 기본 1시간이라 지금 계획 결과는 그대로이고, 3시간과 궤적 검사로 바꾸는 것은 5가 정한다 | 6이 초안 작성, 5가 확인·이후 변경 |
| 베이스라인 예측기 `forecast/baseline.py` | 같은 요일 유형(평일 / 주말·공휴일)과 시간대의 최근 4주 평균 | 6 (완료) |
| LightGBM 예측기 `forecast/lgbm.py` | 같은 인터페이스. 대여·반납 각각 포아송 손실, 재고 0이던 시각은 대여 학습에서 뺀다. 강남-05 검증(2025-04~06): 근무조에 빌 대여소 중 미리 잡은 비율 50.8%(베이스라인 40.7%), 구역 시간별 대여 수 가중 오차율 14.7%(28.3%), 근무조 구간 순유입 오차 3.26~3.35대(3.49~3.53대, 우연에 의한 바닥 약 3.32대). 날씨는 관측값이라 운영(예보)에서는 다시 검증한다 | 6 (강남-05 완료, 전 구역 남음) |

## 5. 나중에 정할 것

- 예측 구간(불확실성) 범위를 같이 줄지. 우선순위 점수의 "임박도"에 쓸 수 있다. M5에 LightGBM과 같이 정한다.
- 대여소 200곳을 한 번에 예측하는 일괄 메서드. 지금 규모에서는 대여소별 호출로 충분하다.
