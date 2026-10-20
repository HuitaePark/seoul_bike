# 공통 solve 명세와 최적화 코어의 연결 검토

검토 기준: main `aebf8a6`의 [AsyncAPI 초안 v0.1](../specs/asyncapi.yaml),
[공개 API 모델](../specs/openapi.yaml), [내부 API](../specs/internal-api.yaml).
공통 스키마 원본은 `docs/specs/`이며, 아래는 역할 5의 검토 의견이다.
이 문서를 추가하면서 공유 스키마를 변경하거나 Kafka 실행기를 구현하지 않았다.

## 바로 대응되는 내용

- `neededBikes` 양수 회수·음수 보충은 `Imbalance.delta`와 같다.
- 트럭의 `capacity`, `load`, `lat`, `lon`은 현재 코어 입력에 대응한다.
- `blockedStationIds`는 진입 불가 제외 조건에 대응한다.
- `solutionLimit`, `serviceMinutesBase`, `serviceMinutesPerBike`는 탐색량·작업 시간에 대응한다.
  분을 초로 변환하는 규칙과 입력 범위는 아래처럼 확정해야 한다.
- 외부 `stationId`·`truckId`는 문자열, 코어 ID는 정수다. 어댑터에서 요청마다
  정렬된 문자열 목록에 내부 인덱스를 부여하고 결과를 원래 문자열로 복원할 수 있다.
  숫자 부분만 잘라내면 `ST-1`과 `ALT-1`이 충돌하므로 그렇게 변환하면 안 된다.

## Kafka 연결 전에 필요한 결정

| 항목 | 현재 공통 초안과 코어 사이의 차이 | 제안·확인 요청 |
| --- | --- | --- |
| 차고지 | `SolveRequest`에 복귀 차고지 좌표가 없음. 코어는 복귀 시간까지 제약에 포함 | 요청에 `depot`을 추가하거나 권역별 고정 설정의 제공·버전 관리 책임 합의 |
| 기준 시각 | 트럭 `availableUntil`은 절대 시각이고 코어는 남은 초·상대 ETA를 사용. 봉투의 `occurredAt`이 스냅샷 기준인지는 불명확 | `asOf` 추가 또는 `occurredAt`의 의미 확정. 남은 시간은 소비 시각이 아닌 같은 입력의 고정 기준으로 변환하고, 큐 지연·실행 기한은 별도로 판정 |
| 재고 입력 | `SolveStation`은 필요량만 포함. 현재 코어는 `bikes`·거치대 수 스냅샷을 받고 회수량이 현재 재고를 넘지 않는지 검증 | `bikes`, `docks`, `observedAt` 등 입력 보완 또는 필요량 사전 검증의 책임을 명시. 어댑터에서 임의 재고를 만들어 채우지 않음 |
| 계획 버전 | requestId에 planVersion이 문자열로 포함되지만 독립 필드·오래된 결과 적용 규칙은 없음 | `basePlanVersion`·입력 스냅샷 버전을 결과와 함께 보존. ID를 해석해 업무 버전을 추론하지 않고 backend가 현재 버전과 비교 |
| 완료·제외 | 공통 요청에는 `blockedStationIds`만 있음. 내부 API에는 `excludeStationIds`, 코어에는 완료 제외도 있음 | backend가 완료/관리자 제외 거점을 후보에서 미리 제거하는지, 필드를 추가하는지 결정. blocked는 미처리, 완료는 미처리에도 제외한다는 차이 유지 |
| 배정 고정 | `fixedStationIds`는 특정 트럭에 남겨 둘 정류장. 현재 코어는 이 제약을 지원하지 않음 | 고정 방문 순서·다음 방문 고정 여부·누락 ID·다중 차량 중복 배정 정책을 확정하고 제약 구현. 필드가 있는 요청을 조용히 무시하지 않음 |
| 수치 범위 | 공통 `priority`·`penaltyScale`은 number, 코어는 정수 비용. 분 단위 서비스 시간도 실수 가능 | 가중치 단위, 고정 배율, 올림 방식·상한을 합의. 음수·비정상 값, 0/음수 solutionLimit, 허용하지 않는 차량 상태를 스키마·어댑터 양쪽에서 거부 |
| 부분 해·실패 | 공통 `status`는 OK/TIMEOUT/FAILED, 코어 상태는 complete/partial/no_solution | 계산 성공과 전량 처리 여부를 나눠 매핑. 부분 계획을 OK로 보낼 수 있으나 미처리와 처리 상태를 보존. no_solution과 인프라 실패를 구분 |
| 결과 필드 | 외부 Stop은 seq·대문자 action·절대 eta, 코어는 순서·소문자 action·상대 초. 외부 Route에는 거리·운행시간 필드가 없음 | 이름·대문자·원래 ID·seq를 변환하고 같은 기준 시각으로 ETA 생성. 거리/시간·남은 필요량·미처리 코드·사용된 우선순위·행렬 출처를 어디에 보존할지 합의 |
| 중복·재시도 | eventId 중복 제거와 requestId 단일 결과가 함께 명시됨. optimizer의 DB 직접 접근은 금지 | 수신 중복과 논리 요청 중복을 구분. 재시작 후 결과 재사용·결과 발행 전 장애·offset commit 순서·동일 ID의 다른 입력 처리 책임을 backend와 합의 |
| 시간 예산 | 내부 API는 최대 60초 후 탐욕 임시안, PRD는 현장 보고부터 변경안 생성까지 10초 | 동기 API 대기 한도와 재경로 목표를 분리. 큐·행렬·솔버·설명 예산, 탐욕안 계산 위치·동일 도로 행렬 사용 여부를 정함 |

## 합의 후 진행 순서

1. 위 입력 누락과 상태·중복 처리 결정을 공통 명세에 반영하고 생산·소비 담당 검토를 받는다.
2. 정상 PLAN/REPLAN, 부분 처리, 완료·제외, 고정 배정, 만료 근무, 중복 요청 fixture를 만든다.
3. 외부 DTO ↔ Python 코어 변환을 단독 검증한다. 미지원 제약은 명시적 입력 오류로 반환한다.
4. Kafka 소비·결과 발행·재시도/DLQ를 연결하고 topics-init과 운영 Compose를 통합한다.

OSRM 공급자·캐시는 이 wire 형식과 분리되어 있어 현재 구현을 유지할 수 있다.
도로 데이터 오류는 `TravelTimeError.code/retryable`을 실행기에서 공통 실패 형식으로 변환한다.
