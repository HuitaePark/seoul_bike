# 최적화 · 인프라 명세

상태: **공통 v0.1 명세 대응 구현, Kafka 연결 전**. 담당: 역할 5.
기준 main: `e819a4b`. 구현: `or-tools/rebalancing/`.

외부 형식은 [asyncapi.yaml](asyncapi.yaml)의 SolveRequest·SolveResult와
[openapi.yaml](openapi.yaml)의 Route·Stop·Unserved가 원본이다.
이 문서는 그 필드를 사용하는 계산 규칙·실행 설정·구현 범위를 설명한다.
전송은 camelCase, Python 내부 dataclass는 snake_case다.

## 책임

`backend → solve.requests → optimizer → solve.results → backend` 순서다.
optimizer는 후보 경로만 계산한다. 계획 ID·버전, 변경 등급, 승인·활성화·감사·업무 갱신은 backend가 담당한다.
optimizer는 DB 직접 접근과 LLM 호출을 하지 않는다.

`contract.solve_request()`는 변환·계산·결과 생성 함수다. Kafka 소비·발행, offset commit,
영속 중복 제거, retry/DLQ와 topics-init은 후속 연결 범위다. 현재 함수는 결과를 저장하지 않는다.

## 공통 요청 대응

| 필드 | 구현·검증 |
| --- | --- |
| eventId·schemaVersion·occurredAt·shiftId·requestId | UUID·버전 1·시간대 있는 시각·상관관계 검증. 유효한 상관관계를 만들 수 없는 봉투는 호출자에 ValueError 전달 |
| traceId | 공통 YAML의 required 목록을 따름. 입력에 있으면 그대로 결과에 전달 |
| kind | PLAN·REPLAN. 재경로는 현재 위치·적재량과 남은 필요량을 받음 |
| stationId·truckId | 원본 문자열을 정렬해 내부 인덱스에 매핑하고 결과에서 복원. 숫자 부분을 잘라 사용하지 않음 |
| lat·lon | 위도 [-90, 90], 경도 [-180, 180], 유한수. 공급자 좌표 순서는 (위도, 경도) |
| neededBikes | 양수 회수·음수 보충, 절댓값 1e9 이내 정수. 0은 방문 작업을 만들지 않음 |
| priority | 공통 number를 비음수 유한수로 수용. 이미 계산된 점수에 부족/회수 가중치를 다시 곱하지 않음 |
| capacity·load | 양의 정수 용량, 0 ≤ load ≤ capacity. 요청 값을 그대로 사용 |
| availableUntil | 시간대 있는 근무 종료 시각. 명시 기준 시각부터 남은 초로 변환하고 소수 초는 내림. 이미 종료된 입력은 오류 |
| fixedStationIds | 처리 시 지정 차량에만 배정. 다른 차량 이동·방문 순서 고정·방문 강제는 하지 않음. 불가능하면 미처리 |
| blockedStationIds | 방문하지 않고 blocked 사유로 미처리. ID는 같은 요청 stations에 있어야 함 |
| params.solutionLimit | 1..1e9 정수. 예시 기본값 50. 해 개수 제한 |
| params.penaltyScale | 비음수 유한수, 생략 시 기존 기본값 3000. 포기 벌점 = ceil(priority × penaltyScale), 미터 비용. 벌점 상한 1e15 |
| params.serviceMinutesBase | 기본 3분. 비음수 유한수, ceil(분 × 60)으로 초 변환 |
| params.serviceMinutesPerBike | 기본 0.5분. 동일한 초 변환. 현장 작업시간 = 기본 초 + 작업 대수 × 대당 초 |

일반 정수·number 입력은 1e9 상한과 변환 후 초·벌점 상한을 검사한다. bool을 정수로 취급하지 않는다.
음수·NaN·무한대·중복 ID·중복 고정 배정·알 수 없는 참조 ID는 거부한다. 알 수 없는 추가 필드는 무시한다.

SolveStation은 **계산된 필요량**이다. RoutingStation으로 받아 재고·거치대 수를 만들거나 요구하지 않는다.
관측 재고에 대한 neededBikes 산출·타당성 검증은 생산자가 담당한다.
기존 예측 데모의 Station 스냅샷 입력은 재고·거치대와 회수 가능량 검증을 계속 수행한다.
완료·관리자 제외 거점은 backend가 남은 후보를 구성할 때 제거한다.
현재 wire에 없는 completedStationIds·excludeStationIds를 새로 정의하지 않는다.

## payload 밖의 필수 실행 설정

v0.1에는 차고지와 입력 스냅샷 기준 시각 필드가 없다. 호출자가
`SolveContext(depot, as_of)`와 `travel_time` 공급자를 명시한다.
실행기는 권역 설정·입력 스냅샷 메타데이터에서 이를 제공해야 한다. 해당 연결은 아직 남아 있다.

- depot: 권역 복귀 차고지 (위도, 경도). 근무시간에 복귀 이동을 포함한다.
- as_of: 고정 계산 기준 시각. 남은 근무시간과 ETA가 모두 같은 기준을 사용한다.
- travel_time: 운영 시 고정 도로 버전의 OSRM·캐시. 개발 추정도 호출자가 명시적으로 선택한다.

데모 차고지·소비 시각·occurredAt을 자동 대입하지 않는다. occurredAt은 메시지 생성 시각이며,
스냅샷 기준과 같은 시각인지는 생산자·실행기가 정한다. 재시도에서도 같은 기준 시각·도로 버전·설정을 사용한다.

## 결과

| 필드 | 생성 규칙 |
| --- | --- |
| eventId·occurredAt | 요청과 다른 새 UUID·결과 생성 시각. ETA 기준 시각과 구분 |
| requestId·shiftId·traceId | 상관관계 보존. traceId는 입력에 있을 때 전달 |
| status | 부분 계획을 포함한 계산 결과는 OK, OSRM 통신 시간 초과·행렬 예산 초과는 TIMEOUT, 잘못된 입력·일반 인프라 실패·해를 못 얻으면 FAILED |
| routes[].truckId·capacity·startLoad | 원래 차량 ID·요청 용량·실제 출발 적재량 |
| stops[].seq·stationId·action·bikes | 1부터 순서, 원래 대여소 ID, PICKUP·DROPOFF, 양의 작업 대수 |
| stops[].loadAfter·eta·status | 작업 후 적재량, as_of + 상대 도착 초를 UTC 시각으로 변환, PENDING |
| unserved[].stationId·priority·reason | 원래 ID·입력 우선순위·미처리 코드 |
| stats.solveMs·bikesMoved | 변환·행렬·솔버·경로 직렬화까지 로컬 시간, 회수·보충 작업 대수 합 |
| error | INVALID_REQUEST, NO_SOLUTION, OSRM 오류 코드와 재시도 가능 여부의 설명 |

OK는 전량 처리 여부가 아니다. unserved를 함께 확인한다.
공개 API Plan의 DRAFT·ACTIVE 등 업무 상태는 backend가 정하며 코어 상태로 덮어쓰지 않는다.
관측하지 않은 stationName·방문 근거 문장을 만들지 않는다. 거리·운행시간·별도 completionStatus는 wire에
임의로 추가하지 않고 코어 Plan에서 유지한다. 선택 필드 objective는 현재 생략한다.

미처리 코드는 blocked, no_trucks, capacity_exceeded, not_selected, no_solution이다.
고정 배정이면 해당 차량 용량으로 capacity_exceeded를 판단한다.
not_selected의 원인을 시간 또는 적재량 하나로 단정하지 않는다.
부분 처리·거점 재방문·차고지 재적재·방문 순서 고정은 지원하지 않는다.

## OSRM · 캐시 · 인프라

OSRM 도로 거리·시간을 조회하며 중복 제거·요청 분할·순서 복원·비대칭 행렬을 지원한다.
도달 불가를 0이나 추정치로 대체하지 않는다. 기본 요청 타임아웃 3초·행렬 예산 8초·스냅 탐색 반경 1km다.
소켓 타임아웃은 전체 업무 흐름의 엄격한 시간 상한을 보장하지 않는다.

캐시는 서버·프로필·도로 버전·스냅 반경·좌표 순서로 구분하며 기본 최대 16개 행렬·TTL 1시간이다.
만료·손상은 재조회하고 디스크 기록 실패는 경고 후 유효한 결과를 유지한다. 도로 변경 시 버전을 갱신한다.
정적 car 프로필은 실시간 교통·트럭 높이/중량을 보장하지 않는다.

[독립 개발 환경](../../infra/optimizer/README.md)은 OSRM 전처리·최적화 컨테이너를 제공한다.
운영 Compose·topics-init·Kafka 연결·실제 지도·서버 부하·전체 10초 검증은 후속 작업이다.
CI는 Python 테스트, 공통 스키마와 호환성, Compose 구성, 전처리 스크립트 구문을 검사한다.

## 예시와 단독 호출

- [request.json](examples/optimizer/request.json): 공통 SolveRequest. 재고·거치대·asOf·depot 추가 없음.
- [result.json](examples/optimizer/result.json): 해당 요청의 SolveResult 예시. solveMs는 성능 보장값이 아님.
- [context.json](examples/optimizer/context.json): **함수 호출 설정 예시**, Kafka 메시지 스키마가 아님.

Python 3.12로 의존성을 설치한 뒤 or-tools/에서 실행한다.

```bash
../.venv/bin/python -m pip install -r ../requirements.txt -r requirements-dev.txt
../.venv/bin/python -m pytest -q
```

uv로 만든 환경은 pip 설치 명령 대신
`uv pip install --python ../.venv/bin/python -r ../requirements.txt -r requirements-dev.txt`를 사용한다.

```python
import json
from datetime import datetime
from pathlib import Path
from rebalancing.contract import SolveContext, solve_request
from rebalancing.travel_time import HaversineTravelTime

examples = Path("../docs/specs/examples/optimizer")
request = json.loads((examples / "request.json").read_text())
settings = json.loads((examples / "context.json").read_text())
context = SolveContext(tuple(settings["depot"]), datetime.fromisoformat(settings["asOf"]))
result = solve_request(request, context=context, travel_time=HaversineTravelTime())
print(json.dumps(result, ensure_ascii=False, indent=2))
```

예시는 개발 추정을 명시적으로 선택한다. 운영 도로 연결에서는 OSRM 공급자로 교체한다.
현장 보고부터 변경안 생성까지 10초는 Kafka·backend·설명을 포함해 별도로 검증한다.
