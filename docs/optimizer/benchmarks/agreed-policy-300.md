# 확정 운행 설정의 N01·300곳 검증 — 2026-10-21

합의 16~20 반영 뒤 로컬 엔진 규모를 확인한다. 거리 ×1.3·시속 20km, 트럭 용량 15대·초기 적재 0대,
정차 180초 + 대당 30초, 가용 3시간·solution-limit=50이다. 이 스트레스 입력은 모든 거점에 5대 작업을 주므로
예측 모델을 호출하지 않는다. 기본 예측 3시간과 명시 1시간의 경계는 별도 단위 검사로 확인한다.

N01은 실제 참조 좌표 265곳과 참조 중심점에 가상 재고/작업량을 결합한다. 중심점은 실제 차고지가 아니다.
300곳은 기존 5개 가상 클러스터(seed=7)의 합성 좌표다. 실제 대여소 300곳을 한 구역으로 합치지 않는다.
두 조건 모두 트럭 5대를 명시했다. 구역 참조 CLI의 trucks_min 기본값 검사와 별개의 성능 조건이다.
REPLAN은 첫 작업을 마친 트럭들이 느린 트럭을 기다린 뒤의 가상 위치·적재·재고·잔여 근무시간이다.
완료 거점과 진입 불가 한 곳을 방문 후보에서 제외하며 현장 관측으로 사용하지 않는다.

Darwin arm64, Python 3.12.13, OR-Tools 9.14.6206 환경이다. 같은 사례는 반복/캐시 조건에 관계없이
전체 계획 해시가 같아야 한다. 실행 순서/외부 부하를 무작위화하거나 완전히 통제한 비교가 아니다.
측정은 행렬 준비를 포함하는 로컬 엔진 호출이며 Kafka 대기·backend·LLM·실제 도로/교통을 포함하지 않는다.
운영 전체 재경로 10초 통과나 서버 자원 예산의 검증으로 사용하지 않는다.

이전 30km/h·거리 보정 없는 [N01 기록](n01-265.md)과 [200곳 native 개선](native-transit.md)은 과거 조건의 자료다.
새 설정은 거리 비용·시간 예산·선택 작업/방문 순서를 바꾸므로 전후 시간을 알고리즘 개선 배율로 비교하지 않는다.
Station.docks 명칭과 이동 설정 namespace가 입력 해시에 반영되어 과거 해시와도 다르다.

측정이 완료된 원시 JSON의 시간 표본·입력/전체 계획 해시·사용한 소스 SHA-256과 프로세스 최대 RSS를 아래에 기록한다.


## 조건별 결과

N01은 none → cold → warm 각 20회, 가상 300곳은 캐시 없는 none 20회로 PLAN/REPLAN을 각각 실행했다.
총 160개 지연 표본이며 사례마다 별도 프로세스다. p95는 nearest-rank(20개 중 정렬한 19번째)다.
입력 구성과 REPLAN 최초 경로 구성(setup_s), warm 사전 호출은 지연 표본에서 제외한다.

| 입력 | 동작 | 후보 | 캐시 | 중앙값 ms | p95 ms | 최대 ms |
| --- | --- | ---: | --- | ---: | ---: | ---: |
| N01 265곳 | PLAN | 265 | none | 3716.65 | 3761.51 | 3769.05 |
| N01 265곳 | PLAN | 265 | cold | 3740.98 | 4106.13 | 4282.39 |
| N01 265곳 | PLAN | 265 | warm | 3699.06 | 3962.11 | 4025.09 |
| N01 265곳 | REPLAN | 259 | none | 8353.83 | 8700.10 | 8970.19 |
| N01 265곳 | REPLAN | 259 | cold | 8402.21 | 9044.37 | 9134.80 |
| N01 265곳 | REPLAN | 259 | warm | 8351.18 | 8839.95 | 8878.59 |
| 가상 300곳 | PLAN | 300 | none | 1750.87 | 1826.14 | 1857.85 |
| 가상 300곳 | REPLAN | 294 | none | 63.54 | 65.75 | 66.35 |

N01 재경로 p95는 **8.70~9.04초**다. 가상 300곳의 재경로가 더 빠른 것은 이 특정 입력이 더 빨리
탐색 종료에 도달한 결과이며, 거점 수가 많을수록 쉬워진다거나 임의의 300곳이 이 시간 안에 풀린다는 뜻이 아니다.
두 지리/작업 스냅샷은 다르므로 서로 속도 개선 배율로 비교하지 않는다. 실제 N01 형태를 함께 남기는 이유다.

모든 사례에서 20회 같은 전체 계획 해시였다. N01은 세 캐시 모드에서도 같았다. N01 none/cold의 공급자 호출은
조건당 20회, warm은 측정 중 0회·캐시 적중 20회다. 행렬 호출 절감과 탐색 시간 절감은 구분한다.

## 처리량·상태·메모리

| 입력/동작 | 완료/차단 | 경과 초 | 작업/미처리 대수 | 거리 m | 최대 RSS MiB |
| --- | --- | ---: | --- | ---: | ---: |
| n01/PLAN | 0/0 | 0 | 635/690 | 60652 | 90.70 |
| n01/REPLAN | 5/1 | 618 | 600/700 | 60484 | 93.53 |
| mock300/PLAN | 0/0 | 0 | 565/935 | 86844 | 96.55 |
| mock300/REPLAN | 5/1 | 1347 | 490/985 | 67350 | 98.31 |

작업 대수는 회수·보충 작업량 합이다. 모든 거점을 입력으로 받았다는 뜻이며 모든 작업을 처리한 결과는 아니다.
REPLAN의 완료된 회수는 이미 대여소 재고·차량 적재에 반영했고 시작 적재량은 모두 5대다.
진입 불가 1곳의 작업량은 미처리에 포함한다. 남은 근무시간·복귀·용량 제약을 그대로 적용했다.

RSS는 Darwin getrusage(RUSAGE_SELF)의 프로세스 최대 bytes 값이다. import·입력 구성·warm 준비와 모든 캐시 조건을
포함한다. 행렬만의 증가량, OSRM/브로커/컨테이너 메모리 또는 운영 서버의 자원 예산은 아니다.

## 원시 표본·재현

각 JSON에 20개 표본, 단계별 시간, 실행 환경, 입력/전체 계획 해시, 측정 소스 8개 SHA-256과 RSS가 있다.
source_base_commit은 2936c16이고 source_sha256이 실제 측정한 추가 구현을 식별한다.
행렬 준비/모델 구성/탐색/결과 추출의 p95는 서로 다른 표본일 수 있어 합산하지 않는다.

- [n01 PLAN 원시 기록](2026-10-21-agreed-n01-plan-5trucks.json)
- [n01 REPLAN 원시 기록](2026-10-21-agreed-n01-replan-5trucks.json)
- [mock300 PLAN 원시 기록](2026-10-21-agreed-mock300-plan-5trucks.json)
- [mock300 REPLAN 원시 기록](2026-10-21-agreed-mock300-replan-5trucks.json)

저장소 or-tools/에서 Python 3.12로 실행한다.

```bash
../.venv/bin/python -m rebalancing.benchmark --region N01 --all-candidates --trucks 5 \
  --repeats 20 --scenario plan --cache-mode compare --profile-stages
../.venv/bin/python -m rebalancing.benchmark --region N01 --all-candidates --trucks 5 \
  --repeats 20 --scenario replan --cache-mode compare --profile-stages
../.venv/bin/python -m rebalancing.benchmark --stations 300 --all-candidates --trucks 5 \
  --repeats 20 --scenario plan --cache-mode none --profile-stages
../.venv/bin/python -m rebalancing.benchmark --stations 300 --all-candidates --trucks 5 \
  --repeats 20 --scenario replan --cache-mode none --profile-stages
```

이 명령은 표본·입력/계획 해시를 재현한다. 소스 해시는 기록된 파일들의 SHA-256, RSS는 각 명령을 별도 프로세스에서
실행해 resource.getrusage(resource.RUSAGE_SELF).ru_maxrss로 추가했다. 측정 시각·호스트 지연은 달라진다.
이전 추정 조건이 필요하면 --speed-kph 30 --detour-factor 1을 명시한다. 이전 모델 필드/해시의 정확한 재현은
해당 기록의 Git 커밋·소스 해시를 따른다.
