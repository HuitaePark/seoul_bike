# 최적화 · 인프라 명세

상태: **계산·Kafka 실행기·지도 검토 구현, 운영 backend 연결 대기**. 담당: 역할 5.
공통 기준은 아래 문서 버전을 따른다.

| 참조 명세 | 문서 버전 |
| --- | --- |
| 공개 API (OpenAPI) | 0.8.0-draft |
| 내부 API (OpenAPI) | 0.5.0-draft |
| Kafka (AsyncAPI) | 0.4.0-draft |

메시지 payload의 schemaVersion은 여전히 **1**이다. 문서 버전과 별도로 관리한다.
아래 구현 설명은 [계산 코드](../../or-tools/rebalancing/)와 같은 저장소의 실행기·화면을 기준으로 한다.

외부 형식은 [asyncapi.yaml](asyncapi.yaml)의 SolveRequest·SolveResult와
[openapi.yaml](openapi.yaml)의 Route·Stop·Unserved가 원본이다.
이 문서는 그 필드를 사용하는 계산 규칙·실행 설정·구현 범위를 설명한다.
전송은 camelCase, Python 내부 dataclass는 snake_case다.

## 책임

`backend → solve.requests → optimizer → solve.results → backend` 순서다.
optimizer는 후보 경로만 계산한다. 계획 ID·버전, 변경 등급, 승인·활성화·감사·업무 갱신은 backend가 담당한다.
optimizer는 DB 직접 접근과 LLM 호출을 하지 않는다.

경로 변경 승인 순서는 backend의 PlanCandidate.requiredApprovers를 따른다.
NONE은 적용하지 않고, MINOR는 기사 승인, MAJOR는 관리자 → 기사 승인 순서다.
도구 앞의 기사 승인이 곧 경로 변경 수락이며 별도 수락 단계를 추가하지 않는다.
optimizer는 grade·requiredApprovers·승인 카드를 생성하지 않는다.
TaskEventMessage의 seq는 backend가 부여하는 작업 이벤트 정보이며 SolveResult에 추가하지 않는다.

`contract.solve_request()`는 변환·계산·결과 생성 함수이며 자체 저장·전송은 하지 않는다.
별도 worker가 Kafka 소비·발행, 결과 파일 저장·중복 제거, offset commit을 맡는다.
독립 개발용 solve·requests.retry·requests.dlq 토픽 초기화와 제한 재시도를 제공한다.
main의 backend SolveGateway·SolveResultConsumer 구현과 실제 worker를 잇는 운영 연결은 후속 범위다.

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

현재 AsyncAPI 0.4.0-draft의 SolveRequest에도 차고지와 입력 스냅샷 기준 시각 필드가 없다. 호출자가
`SolveContext(depot, as_of)`와 `travel_time` 공급자를 명시한다.
실행기는 권역 설정·입력 스냅샷 메타데이터에서 이를 제공해야 한다. 해당 연결은 아직 남아 있다.

현재 worker는 requestId별 shiftId·depot·asOf를 명시한 외부 runtime JSON을 시작 시 읽는다.
설정이 없는 요청은 제한 재시도 후 DLQ로 보낸다. 갱신한 파일은 재시작 후 적용한다. 이 파일은 Kafka 메시지 스키마가 아니며
운영 메타데이터를 자동으로 제공하는 연결은 아직 없다.

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
운영 Compose·backend 연결·운영 retry/DLQ 토픽·관제·실제 지도·서버 부하·전체 10초 검증은 후속 작업이다.
CI는 Python 테스트, 공통 스키마와 호환성, Compose 구성, 전처리 스크립트 구문을 검사한다.

## Kafka 실행기

[Kafka 실행 안내](../../infra/optimizer/KAFKA.md)를 따른다.
키 shiftId 검증, traceId·traceparent 전달, 발행 ACK 후 해당 입력 offset의 동기 commit을 수행한다.
eventId와 requestId의 저장 기록을 확인하며, 같은 requestId의 완료 결과를 새로 생성하지 않는다.
계산 입력이 달라진 ID 재사용은 해당 메시지를 DLQ로 보낸다. 결과 파일은 업무 DB와 별개인 실행기 전용 상태다.

동일한 상태 볼륨의 단일 worker가 전제다. 발행 ACK 직후 상태 저장 전 장애에서는
**같은 결과 eventId가 재전달될 수 있으므로 backend도 eventId 중복 제거가 필요하다.**
분산 exactly-once·수평 확장·상태 볼륨 유실 복구를 보장하지 않는다.
잘못된 봉투·키·ID 내용 충돌은 즉시 solve.requests.dlq로 보낸다. 설정 누락·계산 예외·결과 발행 실패는
solve.requests.retry에서 기본 추가 3회·처리 전 2초 대기 후 재시도하며, 소진 시 DLQ로 보낸다.
본문·키·상관관계는 유지하고 retry 횟수·최초 토픽/파티션/offset·실패 코드를 헤더로 전달한다.
잘못된 retry 메타데이터는 바로 DLQ로 보내고 횟수를 초기화하지 않는다. 전달 ACK 후 원본 offset을 commit한다.
상태 손상·기록 실패, 원본 commit·retry/DLQ 발행·수신 실패는 미완료로 두고 중단한다.
FAILED·TIMEOUT도 같은 requestId의 최종 계산 결과이며, 새 계산은 새 requestId를 쓴다.
retry/DLQ도 ACK와 commit 사이의 장애로 중복될 수 있고 원본과 retry 사이의 전체 순서는 보장하지 않는다.
운영 6파티션 토픽·보존/관제·재처리 절차와 backend 시간 초과 정책은 역할 4와 연결한다.
인자 범위·헤더·운영 한계는 [Kafka 실행 안내](../../infra/optimizer/KAFKA.md#제한-재시도--dlq)를 따른다.

실행기 로그는 요청 상관관계·UTC 기록 시각·실패 단계·처리 시간과 결과 재사용 여부를 JSON으로 남긴다.
durationMs는 저장·발행·offset commit까지이며 Kafka 대기는 제외한다. resultSolveMs는 저장 결과의
과거 계산 시간, computeMs는 이번 처리에서 계산 함수를 호출한 시간이다. 요청 본문·좌표·예외 본문은 기록하지 않는다.
이 로그 필드는 Kafka payload 확장이 아니다. 계산 결과가 FAILED/TIMEOUT이어도 전송을 완료하면 solve.completed다.

benchmark는 초기 계획·재경로와 메모리 캐시 없음/빈 상태/준비 상태를 비교하고,
nearest-rank p95·행렬/공급자/나머지 엔진 시간·표본·입력/계획 해시를 남긴다.
재경로는 초기 계획의 차량별 첫 작업을 마친 뒤 대기하는 가상 스냅샷이며, 위치·적재량·남은 근무시간과
완료·진입 불가 거점을 반영한다. 기본 측정은 하버사인 추정치이며 실제 운영 10초 완료 기준을 대체하지 않는다.

## S05 참조 시드

MCP 소스·실행 환경·검증은 역할 3이 담당하며 [MCP 명세](../MCP.md)를 따른다.
별도 MCP 이미지·고정 의존성·HTTP 검증 환경은 이 최적화 구현에 포함하지 않는다.

역할 6의 ad17528에서 S05 대여소 196곳과 차고지 후보를 추출했다. seeds 모듈은 좌표·중복·거점 수와
대여소 번호/API ID 대응을 검사하며, 재고·필요량·우선순위·기준 시각을 만들지 않는다.
ID와 정책의 팀 확정을 대체하지 않으며, wire 형식도 변경하지 않는다.
[참조 시드 안내](../../infra/optimizer/seeds/README.md)에 원본 커밋·해시·제한을 기록한다.

## 지도·계획 검토 화면

역할 5의 화면은 [별도 명세](plan-review.md)를 따른다. MapLibre·OpenFreeMap 지도·마커, 트럭별 방문 순서·적재량,
대여소 상세와 미처리 사유, 관리자 거점 제외·새 후보 재검토·승인/거절을 공개 API에 맞춰 구현했다.
실제 조회와 로그인 없는 가상 시연을 분리하며, 조회 실패를 시연 데이터로 대체하지 않는다.
공통 인증·API·SSE를 사용하고 업무 DB·내부 API에 직접 접근하지 않는다.
지도 점선은 방문 순서 안내다. 실제 OSRM 도로 경로선과 backend 대여소/계획 API·승인 통합 검증은 남아 있다.

## 예시와 단독 호출

- [request.json](examples/optimizer/request.json): 공통 SolveRequest. 재고·거치대·asOf·depot 추가 없음.
- [result.json](examples/optimizer/result.json): 해당 요청의 SolveResult 예시. solveMs는 성능 보장값이 아님.
- [context.json](examples/optimizer/context.json): **함수 호출 설정 예시**, Kafka 메시지 스키마가 아님.

[실행 안내](../optimizer/README.md)에 따라 Python 3.12 환경을 준비하고 저장소의 `or-tools/`에서 실행한다.

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
