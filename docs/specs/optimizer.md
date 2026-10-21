# 최적화 · 인프라 명세

상태: **계산·Kafka 실행기·지도 검토 구현, 운영 Compose 편입, 실제 입력·도로 통합 대기**. 담당: 역할 5.
공통 기준은 아래 문서 버전을 따른다.

| 참조 명세 | 문서 버전 |
| --- | --- |
| 공개 API (OpenAPI) | 0.13.1-draft |
| 내부 API (OpenAPI) | 0.8.2-draft |
| Kafka (AsyncAPI) | 0.5.1-draft |

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
PR #46으로 운영 Compose에 worker·상태 볼륨·backend healthy 이후 기동·소비자 그룹 헬스체크가 추가됐다.
이동 시간은 하버사인 추정치다. PR #89로 `/internal/plans/solve`의 실제 입력 조립·요청/응답·탐욕 임시안이
main에 구현됐다. 실데이터 적재·실제 worker와 승인까지의 전체 운영 검증은 별도다.

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
| blockedStationIds | 방문하지 않고 BLOCKED 사유로 미처리. ID는 같은 요청 stations에 있어야 함 |
| params.solutionLimit | 1..1e9 정수. 예시 기본값 50. 해 개수 제한 |
| params.penaltyScale | 비음수 유한수, 생략 시 기존 기본값 3000. 포기 벌점 = ceil(priority × penaltyScale), 미터 비용. 벌점 상한 1e15 |
| params.serviceMinutesBase | 기본 3분. 비음수 유한수, ceil(분 × 60)으로 초 변환 |
| params.serviceMinutesPerBike | 기본 0.5분. 동일한 초 변환. 현장 작업시간 = 기본 초 + 작업 대수 × 대당 초 |

일반 정수·number 입력은 1e9 상한과 변환 후 초·벌점 상한을 검사한다. bool을 정수로 취급하지 않는다.
음수·NaN·무한대·중복 ID·중복 고정 배정·알 수 없는 참조 ID는 거부한다. 알 수 없는 추가 필드는 무시한다.

SolveStation은 **계산된 필요량**이다. RoutingStation으로 받아 재고·거치대 수를 만들거나 요구하지 않는다.
관측 재고에 대한 neededBikes 산출·타당성 검증은 생산자가 담당한다.
기존 예측 데모의 Station 스냅샷 입력은 재고·거치대와 회수 가능량 검증을 계속 수행한다.
완료 거점은 backend가 남은 후보를 구성할 때 제거한다. 관리자 제외·현장 차단은 `stations`에 남기고
`blockedStationIds`로 지정해야 `BLOCKED` 미처리로 반환된다. stations에 없는 차단 ID는 거부한다.
현재 wire에 없는 completedStationIds·excludeStationIds를 새로 정의하지 않는다.

## backend 입력·후보 저장과의 경계 (PR #89)

- backend는 asOf를 현재 시각과 근무조 시작 중 늦은 시각으로, availableUntil을 근무조 끝으로 보낸다.
  운영 요청의 asOf는 비정시도 가능하다. 아래 로컬 예측기의 정시 조건을 이미 계산된 neededBikes 요청에 적용하지 않는다.
- PLAN은 구역의 실제 활성 트럭·차고지·적재 0을, REPLAN은 마지막 DONE 위치·적재량과 남은 근무시간을 보낸다.
  완료 정류장과 재계산하지 않는 트럭의 작업은 생산자가 후보에서 제거하고, 처리 차량을 유지할 작업만 fixedStationIds로 보낸다.
- optimizer 결과는 이번 계산의 방문만 seq 1부터 담는다. 사용하지 않은 트럭은 결과에서 생략할 수 있다.
  backend가 입력 차량의 빈 경로를 채우고, REPLAN에서는 이전 비-PENDING 정류장 뒤에 새 방문을 병합한다.
  최종 seq는 이전 정류장의 최대 seq 다음부터이며 전체 계획·이전 startLoad·재계산하지 않은 트럭은 backend가 보존한다.
- optimizer의 FAILED/TIMEOUT 또는 60초 대기는 backend가 같은 입력의 탐욕 임시안으로 처리한다.
  PlanCandidate의 TIMEOUT_PROVISIONAL·plan.provisional·explanation, 승인·후보 활성화는 backend 책임이다.
  optimizer가 별도 PlanCandidate를 만들거나 저장 결과의 차량/seq를 확장하지 않는다.
- priority는 backend가 계산한 점수를 그대로 받으며 기본 penaltyScale만 생략 시 3000을 사용한다.
  내부 API의 요청/응답·오류·제약 상세는 [internal-api.yaml](internal-api.yaml)의 /internal/plans/solve를 따른다.

## 로컬 수요 예측 연결

`DemandPredictor.predict(station, start, horizon_h)`는 `start`, `rentals_h`, `returns_h`,
`model_version`을 제공한다. `forecast/`의 별도 Forecast 클래스도 같은 필드로 직접 받을 수 있으며,
공통 모델 파일이나 학습 의존성을 optimizer 이미지에 합치지 않는다. 호출자가 미리 준비한 예측기를 주입한다.

- `start`는 명시적인 정시다. 시간대 없는 값은 데이터 파일과 같은 KST이며, 시간대 있는 값은 KST로 변환한 뒤
  시간대를 제거해 데이터 예측기를 호출한다. 날짜를 현재 시각에서 만들거나 비정시 입력을 임의로 내리지 않는다.
- `horizon_h`는 1~5 정수다. 기본은 합의 16의 3시간이며 1~5시간 명시 입력도 유지한다.
- 두 시간별 배열의 길이·시작 시각·모델 버전과 비음수 유한 실수 값을 검사한다. NumPy 실수 출력도 수용한다.
- 합계는 시간별 값의 Decimal 표현으로 더한다. 대여·반납을 각각 먼저 반올림하지 않는다.
  구간 끝 목표 하한 `max(0, rentals - returns + safety)`는 올림하고,
  상한 `max(0, operating_limit + rentals - returns)`은 내림해 정수 필요량을 만든다.
  목표 상한보다 안전 하한이 크면 기존처럼 하한을 우선하며, 거치대 수로 재고나 필요량을 잘라내지 않는다.
- 이 단계는 기존 **구간 끝 재고 정책**이다. 시간별 배열을 받더라도 중간의 빈 시각·재고 궤적 안전을 보장하지 않는다.
  기본 3시간 전환은 반영했다. 시간별 중간 검사는 합의 16의 M4 후속이다.
  backend 내부 API 0.8의 StationRisk는 관측 시각 기준 궤적으로 별도 계산한다. wire의 neededBikes/priority를
  로컬 구간 끝 정책으로 덮어쓰지 않는다. 비정시 로컬 예측 정렬은 생산자와 확인한다.

데이터 예측기의 Station.id는 대여소번호를 사용한다. API·Kafka의 stationId는 ADR 0001의 `ST-` ID이며
서로 숫자가 다르므로, 내보내는 생산자가 ref.stations/CSV 대응표로 변환한다. 접두사를 붙이거나 잘라 변환하지 않는다.

이는 로컬 예측·시뮬레이션 연결이며 Kafka worker의 역할을 확장하지 않는다.
worker는 계속 이미 계산된 `neededBikes`를 받는다. 운영 수요·우선순위·재고 입력 생성과 예측 DB 적재는
역할 4·6의 경계에서 연결하며 공개 API·Kafka 메시지 필드는 추가하지 않는다.

## 메시지의 계산 기준과 이동 공급자

SolveRequest의 필수 `depot {lat, lon}`·`asOf`에서 SolveContext를 만든다.
requestId별 runtime JSON·`--contexts`·별도 차고지 예시는 사용하지 않는다.

- depot: 권역 복귀 차고지. (위도, 경도)로 검증하며 복귀 이동을 근무시간에 포함한다.
- asOf: 시간대가 있는 RFC 3339 계산 기준 시각. 남은 근무시간과 ETA는 이 시각부터 계산한다.
- travel_time: 호출자가 명시하는 이동 공급자. 운영은 고정 도로 버전의 OSRM·캐시, 개발은 하버사인 추정치를 선택한다.

생산자는 권역 차고지와 실제 입력 스냅샷 시각을 메시지에 넣는다. 소비 시각·occurredAt·데모값으로 대체하지 않는다.
봉투가 유효하면 depot/asOf 누락·잘못된 값은 FAILED·INVALID_REQUEST·retryable=false로 반환한다.
재시도에서도 메시지의 같은 기준 시각·차고지와 같은 도로 버전을 사용한다.

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
| code·retryable·error | FAILED/TIMEOUT에서 모두 필수. 고정 실패 코드·새 requestId로 재계산할 만한지·사람이 읽는 설명 |

OK는 전량 처리 여부가 아니다. unserved를 함께 확인한다.
공개 API Plan의 DRAFT·ACTIVE 등 업무 상태는 backend가 정하며 코어 상태로 덮어쓰지 않는다.
관측하지 않은 stationName·방문 근거 문장을 만들지 않는다. 거리·운행시간·별도 completionStatus는 wire에
임의로 추가하지 않고 코어 Plan에서 유지한다. 선택 필드 objective는 현재 생략한다.

외부 미처리 코드는 BLOCKED, NO_TRUCKS, CAPACITY_EXCEEDED, NOT_SELECTED, NO_SOLUTION이다.
내부 계산 코드는 기존 소문자를 유지하고 공통 응답에서 대문자로 변환한다.
고정 배정이면 해당 차량 용량으로 CAPACITY_EXCEEDED를 판단한다.
NOT_SELECTED의 원인을 시간 또는 적재량 하나로 단정하지 않는다.

실패 코드는 INVALID_REQUEST, NO_SOLUTION, OSRM_BUDGET_EXCEEDED, OSRM_TIMEOUT,
OSRM_DATA_CHANGED, OSRM_UNREACHABLE, OSRM_INVALID_RESPONSE, OSRM_HTTP_ERROR,
OSRM_NETWORK_ERROR, OSRM_REJECTED다. 내부 osrm_error는 OSRM_REJECTED로 변환한다.
TIMEOUT은 예산 초과·통신 시간 초과이며 그 외는 FAILED다. error 문자열로 분기하지 않는다.
retryable은 새 requestId의 재계산에 대한 정보이며 정상 FAILED/TIMEOUT을 전달 재시도하지 않는다.
부분 처리·거점 재방문·차고지 재적재·방문 순서 고정은 지원하지 않는다.

## OSRM · 캐시 · 인프라

OSRM 도로 거리·시간을 조회하며 중복 제거·요청 분할·순서 복원·비대칭 행렬을 지원한다.
도달 불가를 0이나 추정치로 대체하지 않는다. 기본 요청 타임아웃 3초·행렬 예산 8초·스냅 탐색 반경 1km다.
소켓 타임아웃은 전체 업무 흐름의 엄격한 시간 상한을 보장하지 않는다.

캐시는 서버·프로필·도로 버전·스냅 반경·좌표 순서로 구분하며 기본 최대 16개 행렬·TTL 1시간이다.
만료·손상은 재조회하고 디스크 기록 실패는 경고 후 유효한 결과를 유지한다. 도로 변경 시 버전을 갱신한다.
정적 car 프로필은 실시간 교통·트럭 높이/중량을 보장하지 않는다.

[독립 개발 환경](../../infra/optimizer/README.md)은 OSRM 전처리·최적화 컨테이너를 제공한다.
운영 worker는 Compose에 편입됐고 실제 입력 조립은 PR #89의 backend가 맡는다. 실데이터 적재·OSRM 운영 연결·
retry/DLQ 관제·서버 부하·전체 10초 검증은 후속 작업이다.
CI는 Python 테스트, 공통 스키마와 호환성, Compose 구성, 전처리 스크립트 구문을 검사한다.

## Kafka 실행기

[Kafka 실행 안내](../../infra/optimizer/KAFKA.md)를 따른다.
키 shiftId 검증, traceId·traceparent 전달, 발행 ACK 후 해당 입력 offset의 동기 commit을 수행한다.
결과·retry·DLQ 발행은 Java producer와 같은 `murmur2_random`을 사용한다. 같은 키·같은 파티션 수에서
파티션 번호를 맞추며 토픽 사이의 전체 순서나 기존 레코드의 재배치를 보장하지 않는다.
eventId와 requestId의 저장 기록을 확인하며, 같은 requestId의 완료 결과를 새로 생성하지 않는다.
계산 입력이 달라진 ID 재사용은 해당 메시지를 DLQ로 보낸다. 결과 파일은 업무 DB와 별개인 실행기 전용 상태다.

동일한 상태 볼륨의 단일 worker가 전제다. 발행 ACK 직후 상태 저장 전 장애에서는
**같은 결과 eventId가 재전달될 수 있으므로 backend도 eventId 중복 제거가 필요하다.**
분산 exactly-once·수평 확장·상태 볼륨 유실 복구를 보장하지 않는다.
잘못된 봉투·키·ID 내용 충돌은 즉시 solve.requests.dlq로 보낸다. 계산 함수의 예외·결과 발행 실패는
solve.requests.retry에서 기본 추가 3회·처리 전 2초 대기 후 재시도하며, 소진 시 DLQ로 보낸다.
본문·키·상관관계는 유지하고 retry 횟수·최초 토픽/파티션/offset·실패 코드를 헤더로 전달한다.
잘못된 retry 메타데이터는 바로 DLQ로 보내고 횟수를 초기화하지 않는다. 전달 ACK 후 원본 offset을 commit한다.
상태 손상·기록 실패, 원본 commit·retry/DLQ 발행·수신 실패는 미완료로 두고 중단한다.
FAILED·TIMEOUT도 같은 requestId의 최종 계산 결과이며, 새 계산은 새 requestId를 쓴다.
retry/DLQ도 ACK와 commit 사이의 장애로 중복될 수 있고 원본과 retry 사이의 전체 순서는 보장하지 않는다.
backend가 요청 retry/DLQ 토픽도 app.kafka.partitions(기본 6)로 생성한다. 운영 보존/관제·재처리 절차와 backend 시간 초과 정책은 역할 4와 연결한다.
인자 범위·헤더·운영 한계는 [Kafka 실행 안내](../../infra/optimizer/KAFKA.md#제한-재시도--dlq)를 따른다.

실행기 로그는 요청 상관관계·UTC 기록 시각·실패 단계·처리 시간과 결과 재사용 여부를 JSON으로 남긴다.
durationMs는 저장·발행·offset commit까지이며 Kafka 대기는 제외한다. resultSolveMs는 저장 결과의
과거 계산 시간, computeMs는 이번 처리에서 계산 함수를 호출한 시간이다. 요청 본문·좌표·예외 본문은 기록하지 않는다.
이 로그 필드는 Kafka payload 확장이 아니다. 계산 결과가 FAILED/TIMEOUT이어도 전송을 완료하면 solve.completed다.

benchmark는 초기 계획·재경로와 메모리 캐시 없음/빈 상태/준비 상태를 비교하고,
nearest-rank p95·행렬/공급자/나머지 엔진 시간·표본·입력/계획 해시를 남긴다.
`--profile-stages`는 입력/행렬 준비·모델 구성·탐색·결과 정리의 표본·중앙값·p95를 별도로 기록한다.
거리·작업시간·적재 변화는 native 행렬/벡터로 전달해 탐색 중 Python 반복 평가를 줄인다.
공급자/캐시 행렬·탐색 설정·차량 제약은 유지하며 진단 시간을 공통 Plan/SolveResult에 추가하지 않는다.
[성능 비교](../optimizer/benchmarks/native-transit.md)에 기준과의 전체 계획 동등성·전후 원시 표본·한계를 기록한다.
재경로는 초기 계획의 차량별 첫 작업을 마친 뒤 대기하는 가상 스냅샷이며, 위치·적재량·남은 근무시간과
완료·진입 불가 거점을 반영한다. 기본 측정은 하버사인 추정치이며 실제 운영 10초 완료 기준을 대체하지 않는다.

## 서울 17개 구역 참조 시드

MCP 소스·실행 환경·검증은 역할 3이 담당하며 [MCP 명세](../MCP.md)를 따른다.
별도 MCP 이미지·고정 의존성·HTTP 검증 환경은 이 최적화 구현에 포함하지 않는다.

역할 6의 기준 CSV를 직접 읽어 서울 17개 구역·2,716곳을 검증한다. 구역 간 ID 중복·미등록 구역 참조·
구역별 대여소 수·좌표·거치대·역할과 대여소번호/API ID 대응을 검사한다.
wire ID는 ADR-0001에 따라 `api_station_id`(`ST-`)이며 대여소번호에 접두사를 붙여 만들지 않는다.
재고·필요량·우선순위·기준 시각을 만들거나 참조 중심점을 운영 차고지로 자동 적용하지 않는다.
[참조 시드 안내](../../infra/optimizer/seeds/README.md)에 원본 커밋·해시·제한을 기록한다.
가장 큰 N01(265곳)의 가상 작업량 성능은 [별도 기록](../optimizer/benchmarks/n01-265.md)에 둔다.
합의 18에서 NFR-01은 300곳으로 확정됐고 PRD 반영은 기획 리드가 맡는다. 이 기록은 265곳이며
확정 운행 설정의 N01·가상 300곳 검사는 [후속 기록](../optimizer/benchmarks/agreed-policy-300.md)에 둔다.
실제 도로·전체 흐름의 10초 검증은 후속이다.

## 확정 기본값 적용

`Station.docks`는 거치대 수이며 `Truck.capacity`는 적재 용량이다. 대여소의 이전 `capacity=` 키워드는
`docks=`로 바꾼다. 위치 인자 순서는 같고 운영 상한은 별도 `operating_limit`이다. 시뮬레이터 소비처도 함께 바꿨다.
하버사인 추정은 거리 ×1.3·시속 20km이며 거리와 시간을 각각 올림한다. OSRM의 도로 거리·시간은 그대로 사용한다.
구역 참조 benchmark는 `--trucks` 생략 시 `trucks_min`을 쓴다. 가용 차량을 운영 메시지에 만드는 책임은 생산자에게 있다.
합의 30의 운영 트럭 ID는 ref.trucks의 TR-NN이다. 코어는 내부 숫자 ID를 쓰고 wire에서는 생산자가 준 문자열을 그대로 복원한다.

## 지도·계획 검토 화면

역할 5의 화면은 [별도 명세](plan-review.md)를 따른다. MapLibre·OpenFreeMap 지도·마커, 트럭별 방문 순서·적재량,
대여소 상세와 미처리 사유, 관리자 거점 제외·새 후보 재검토·승인/거절을 공개 API에 맞춰 구현했다.
실제 조회와 로그인 없는 가상 시연을 분리하며, 조회 실패를 시연 데이터로 대체하지 않는다.
공통 인증·API·SSE를 사용하고 업무 DB·내부 API에 직접 접근하지 않는다.
지도 점선은 방문 순서 안내다. 실제 OSRM 도로 경로선과 backend 대여소/계획 API·승인 통합 검증은 남아 있다.

## 예시와 단독 호출

- [request.json](examples/optimizer/request.json): depot·asOf를 포함한 공통 SolveRequest. 재고·거치대 필드는 추가하지 않음.
- [result.json](examples/optimizer/result.json): 해당 요청의 SolveResult 예시. solveMs는 성능 보장값이 아님.

[실행 안내](../optimizer/README.md)에 따라 Python 3.12 환경을 준비하고 저장소의 `or-tools/`에서 실행한다.

```bash
../.venv/bin/python -m pip install -r ../requirements.txt -r requirements-dev.txt
../.venv/bin/python -m pytest -q
```

uv로 만든 환경은 pip 설치 명령 대신
`uv pip install --python ../.venv/bin/python -r ../requirements.txt -r requirements-dev.txt`를 사용한다.

```python
import json
from pathlib import Path
from rebalancing.contract import solve_request
from rebalancing.travel_time import HaversineTravelTime

examples = Path("../docs/specs/examples/optimizer")
request = json.loads((examples / "request.json").read_text())
result = solve_request(request, travel_time=HaversineTravelTime())
print(json.dumps(result, ensure_ascii=False, indent=2))
```

예시는 개발 추정을 명시적으로 선택한다. 운영 도로 연결에서는 OSRM 공급자로 교체한다.
현장 보고부터 변경안 생성까지 10초는 Kafka·backend·설명을 포함해 별도로 검증한다.
