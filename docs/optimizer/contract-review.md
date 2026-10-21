# 공통 solve 명세 대응 기록

공개 API 0.15.0-draft, 내부 API 0.8.5-draft·Kafka 0.5.1-draft를 따른다.
공유 YAML을 유지하고 구현 코드·문서를 맞췄다. 메시지 schemaVersion은 1이다.
역할 명세는 [optimizer.md](../specs/optimizer.md)다.

| 이전 차이 | 현재 대응 |
| --- | --- |
| snake_case 독자 요청 | 공통 camelCase 요청·결과 사용, 이전 예시는 공통 명세 안내로 교체 |
| 문자열 외부 ID | 전체 문자열 정렬·매핑·복원, 접두사가 다른 ID의 숫자 부분 충돌 방지 |
| 공통 입력에 재고·거치대 없음 | RoutingStation으로 computed neededBikes 수용. 스냅샷을 만들지 않으며 생산자가 필요량 검증 |
| 실수 priority·penaltyScale | 비음수 유한수 지원, Decimal 곱 후 정수 벌점으로 올림 |
| 분 단위 시간·절대 근무 종료 | 작업 초 올림, 명시 시각부터 남은 초 내림, +09:00 ETA 생성 |
| fixedStationIds 미지원 | 두 정책에 차량 배정 제한 구현. 불가능하면 다른 차량으로 옮기지 않고 미처리 |
| 결과 차이 | OK/TIMEOUT/FAILED, seq·대문자 action·+09:00 eta·원본 ID·미처리 priority/reason으로 변환 |
| 공통에 없는 결과 필드 | 거리·기간·별도 처리 상태를 wire에 추가하지 않고 코어에서 유지 |

차고지·기준 시각은 메시지의 depot·asOf에서 읽는다. 도로 공급자는 travel_time으로 명시한다.
requestId별 runtime 파일 의존을 제거했다. PR #46으로 worker는 운영 Compose에 편입됐으며,
PR #89로 실제 구역·스냅샷·트럭 입력 조립과 backend 탐욕 임시안 처리가 main에 구현됐다.
실데이터 적재 및 worker와 실제 승인까지의 전체 운영 검증은 남아 있다.
독립 worker는 Kafka 결과 발행·offset commit과 단일 상태 볼륨의 파일 중복 방지를 구현했다.
발행 직후 장애에서는 같은 결과 eventId가 재전달될 수 있어 backend 소비자 중복 제거도 필요하다.
worker의 요청 제한 재시도·DLQ를 구현했고 backend가 요청 retry/DLQ 토픽도 생성한다. 운영 상태 저장·관제·배포 정책은 담당자와 연결해야 한다.
순수 계산 함수 자체는 저장하지 않는다.

PR #108 현장 보고 후 입력도 두 정책에서 검증한다. 마지막 DONE 위치와 실제 load, 남은 고정 작업을 사용하고
완료 대여소를 다시 만들지 않는다. SKIPPED 차단 대여소는 방문 없이 BLOCKED로 반환한다.
이 검사는 backend가 조립한 형태의 합성 입력이며 Java/DB 왕복 검증은 아니다.

테스트는 공통 YAML을 직접 읽어 정상·부분 처리·오류·타임아웃 결과를 검증한다.
실제 도로 품질·서버 배포·전체 응답시간 완료를 의미하지 않는다.

## 최신 명세 반영

PR #34의 SolveRequest 필수 depot·asOf, 실패 결과의 code·retryable·error,
Unserved.reason 대문자 enum을 반영했다. 내부 계산·탐색 정책은 유지한다.
미처리 사유를 표시하는 화면·시연 데이터와 예시·테스트도 같은 계약을 따른다.
ai.events 재시도/DLQ와 solve.results 재시도/DLQ는 backend가 담당한다.
요청 측 solve.requests.retry → .dlq는 역할 5 worker가 처리한다. 횟수·헤더·실패 분류는
[실행 안내](../../infra/optimizer/KAFKA.md#제한-재시도--dlq)에 기록하며 공통 payload는 유지한다.
경로 변경 승인 순서는 backend의 requiredApprovers를 따르며 optimizer가 결정하지 않는다.
작업 threadId는 backend가 정하는 작업 단위 값이며 optimizer의 메시지 키는 계속 shiftId다.
새 TaskEvent.code를 SolveResult에 추가하지 않는다.
작업 이벤트 seq도 backend가 부여하므로 SolveResult에 추가하지 않는다.

공개 API v0.12의 계정 상태·조회 권한과 v0.11의 미처리 사유 값 목록을 따른다.
계획 검토의 예측 구간은 합의 29에 맞춰 최대 5시간으로 제한한다.
명세·예시와 역할 5 구현·인프라는 같은 저장소에 있으며, [실행 안내](README.md)도 상대 경로로 연결한다.


## 내부 API 0.8.2 연결

생산자의 비정시 asOf, EVE 종료 21:00, 필요한 작업만의 ST-ID·TR-NN, 계산된 priority,
생략된 penaltyScale/fixedStationIds와 blockedStationIds를 공통 입력 그대로 받는다.
재계산 결과 seq는 이번 계산의 1부터다. 이전 방문 병합·최대 seq 뒤 번호 부여·빈 차량 경로는 backend가 보존한다.
탐욕 임시안은 backend의 PlanCandidate이며 worker의 SolveResult 상태를 확장하지 않는다.
