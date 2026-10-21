# 경로 탐색의 Python 반복 호출 제거 — 2026-10-21

`hyunmoo`의 f297ac4를 기준으로 계산 결과를 유지하면서 평가 비용을 줄였다.
가상 200곳·5대 재경로의 p95는 **5984.98ms → 1043.75ms**로 줄었다(이번 로컬 표본에서 약 5.73배).
각 조건 20회 모두 같은 입력·계획 해시, 작업량·미처리량·거리를 유지했다.
실제 도로·서버·Kafka·backend·LLM을 포함한 운영 10초 완료를 판정하는 결과는 아니다.

## 변경과 근거

기존 cProfile에서 전체 5회 계산(초기 PLAN, warm 준비, REPLAN 3개 표본) 동안
time_cb 56,230,864회, distance_cb 26,546,738회, IndexToNode 165,707,326회가 호출됐다.
작업시간과 인덱스 변환을 매 간선 평가마다 Python에서 반복했다.
이 진단 실행에는 cProfile 비용이 추가되므로 그 시간을 아래 성능 표에 사용하지 않았다.

거리·작업시간 포함 이동시간·적재 변화는 한 번 준비한 뒤 OR-Tools의 native 평가기에 전달한다.
[고정 버전의 RegisterTransitMatrix·RegisterUnaryTransitVector 구현](https://github.com/google/or-tools/blob/v9.14/ortools/constraint_solver/routing.cc#L593)은
노드 기준 행렬·벡터를 받아 C++에서 조회한다. 탐색 정책·해 개수·벌점·차량 제약은 유지한다.
공급자·캐시의 원본 행렬은 복사해 사용한다. 별도 차량 시작 노드에서 바로 종료하는 경우만
이동 비용을 0으로 두며, 실제 방문 차량의 복귀 거리·시간은 유지한다.

native 등록을 위한 행렬 복사로 모델 구성 시간·메모리가 늘어나는 대신 반복 호출 비용을 줄인다.
아래 모델 구성 p95는 PLAN 0.49→1.17ms, REPLAN 0.60→1.30ms다.
행렬 크기에 따라 추가 메모리가 증가하며, 200곳을 벗어난 규모의 성능·메모리는 측정하지 않았다.

## 조건과 결과

- Darwin arm64, Python 3.12.13, OR-Tools 9.14.6206, seed=7, solution-limit=50.
- 가상 대여소 200곳·트럭 5대, 모든 대여소를 작업 후보로 둔 스트레스 입력, 하버사인 추정치.
- 각 PLAN/REPLAN에서 준비된 메모리 캐시(warm)로 20회. 측정 중 공급자 호출 0회, 캐시 적중 20회.
- REPLAN은 첫 작업을 마친 뒤 대기하는 가상 스냅샷이다. 완료 5곳·차단 1곳을 제외한 후보 194곳,
  경과 1091초·차량 적재량 각 5대다. 현장 관측값으로 사용하지 않는다.
- 기준 REPLAN → 기준 PLAN → 개선 PLAN → 개선 REPLAN 순서로 별도 실행했다.
  순서를 무작위로 섞거나 외부 부하를 완전히 통제한 실험은 아니므로 배율을 일반 성능 보장으로 쓰지 않는다.

| 시나리오 | 구현 | 중앙값 ms | p95 ms | 최대 ms | 탐색 p95 ms |
| --- | --- | ---: | ---: | ---: | ---: |
| PLAN | Python callbacks | 81.55 | 84.77 | 84.94 | 81.34 |
| PLAN | native 행렬 | 28.60 | 29.37 | 29.88 | 24.80 |
| REPLAN | Python callbacks | 5801.43 | 5984.98 | 6295.91 | 5981.55 |
| REPLAN | native 행렬 | 1011.43 | 1043.75 | 1055.23 | 1039.34 |

표본의 p95는 nearest-rank(20개 중 정렬한 19번째)다. 구간별 p95는 서로 다른 표본일 수 있어 합산하지 않는다.
PLAN은 전후 모두 작업 600대·미처리 400대·103125m,
REPLAN은 작업 560대·미처리 415대·85523m다. 작업 대수는 회수·보충 작업량의 합이다.

| 시나리오 | 입력 SHA-256 | 전체 계획 SHA-256 |
| --- | --- | --- |
| PLAN | d9b1431c72b9c807166633692f1a676f559a3c3f359356f260b1f1e3f195abd9 | 0d0e2e49537dbb045e2c176c32adc316531f260565a3f40a021990a49bebf2b0 |
| REPLAN | 1cc86965239b1452eb3361f18ac54a3e222793839435749e72506b10e594daf3 | 6c32a3915a4cd2c186cb944bef91cc7bfb3c3dddea1e2c2f5fa2417af0de0708 |

전체 계획 해시에는 차량·방문 순서·도착/출발 시각·적재량·미처리 사유도 포함된다.
단위 검사에서는 seed 1/7/19 × PLAN/REPLAN × solution-limit 1/50의 12조건에서
같은 OR-Tools 런타임의 Python 콜백 등록과 native 등록 결과 전체를 비교한다.
별도 물리 검사로 비대칭 행렬·실제 복귀·작업시간·원격 미사용 차량과 원본 행렬 재사용을 확인한다.

## 단계 측정과 재현

`--profile-stages`를 주면 각 표본의 다음 시간을 초 단위로 추가한다.
Python 호출자는 `plan_routes(..., timings={})`로 받을 수 있으며 이 딕셔너리는 호출마다 초기화한다.
진단 시간은 Plan·SolveResult·공통 스키마에 추가하지 않는다.

| stages | 포함 범위 |
| --- | --- |
| prepare_problem | 입력 검증·정렬·후보 구성·행렬 공급/캐시·행렬 검증 |
| build_model | 거리/시간 행렬 준비·native 등록·용량/시간 차원·벌점·탐색 설정 |
| search | SolveWithParameters. 기준 구현에서는 Python 평가 콜백도 포함 |
| extract_plan | 경로·방문·적재량·복귀·미처리 사유를 Plan으로 정리 |

timings.matrix는 prepare_problem 안에, timings.provider는 matrix 안에 포함된다.
단계 합계와 elapsed_s 사이에는 측정 기록·함수 반환·native 객체 정리 등의 시간이 남을 수 있다.
작업이나 차량이 없으면 build_model/search는 0이며 공급자와 솔버를 호출하지 않는다.
실패한 호출은 예외로 끝나며 성공 표본으로 기록하지 않는다.

개선 버전에서 `or-tools/` 기준:

```bash
../.venv/bin/python -m rebalancing.benchmark --stations 200 --trucks 5 --repeats 20 \
  --all-candidates --scenario replan --cache-mode warm --profile-stages
```

PLAN은 `--scenario plan`으로 바꾼다. 기존 옵션과 기본 출력은 유지한다.
기준 버전은 별도 f297ac4 체크아웃에서 같은 명령을 `--profile-stages` 없이 실행할 수 있다.
기준의 단계별 측정까지 재현하려면 [측정용 패치](native-baseline-instrumentation.patch)를
그 별도 체크아웃에 `git apply --unidiff-zero`로 적용한다. 패치는 시간 기록만 추가하고
콜백 계산·탐색 설정은 유지한다.
패치를 실제 적용해 원시 기록의 소스 해시와 일치함을 확인했다.

## 원시 기록

각 JSON은 20개 원시 표본·입력/계획 해시·실행 환경·측정 코드와 planner 소스 해시를 포함한다.
기준 코드는 f297ac4에 위 측정용 패치만 적용한 것이다.

- [기준 PLAN](2026-10-21-native-before-plan-200x5.json) · [개선 PLAN](2026-10-21-native-after-plan-200x5.json)
- [기준 REPLAN](2026-10-21-native-before-replan-200x5.json) · [개선 REPLAN](2026-10-21-native-after-replan-200x5.json)
- [cProfile 진단 요약](2026-10-21-native-cprofile.json): 전후 같은 5회 계산. imports·준비·warm 포함, 지연 표본과 별도.

다음 검증은 확정 권역의 실제 OSRM 도로·캐시·서버 부하와 backend 요청을 연결한 전체 흐름이다.
