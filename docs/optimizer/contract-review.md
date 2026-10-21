# 공통 solve 명세 대응 기록

공개 API 0.8.0-draft, 내부 API 0.5.0-draft·Kafka 0.4.0-draft를 따른다.
공유 YAML을 유지하고 구현 코드·문서를 맞췄다. 메시지 schemaVersion은 1이다.
역할 명세는 [optimizer.md](../specs/optimizer.md)다.

| 이전 차이 | 현재 대응 |
| --- | --- |
| snake_case 독자 요청 | 공통 camelCase 요청·결과 사용, 이전 예시는 공통 명세 안내로 교체 |
| 문자열 외부 ID | 전체 문자열 정렬·매핑·복원, 접두사가 다른 ID의 숫자 부분 충돌 방지 |
| 공통 입력에 재고·거치대 없음 | RoutingStation으로 computed neededBikes 수용. 스냅샷을 만들지 않으며 생산자가 필요량 검증 |
| 실수 priority·penaltyScale | 비음수 유한수 지원, Decimal 곱 후 정수 벌점으로 올림 |
| 분 단위 시간·절대 근무 종료 | 작업 초 올림, 명시 시각부터 남은 초 내림, UTC ETA 생성 |
| fixedStationIds 미지원 | 두 정책에 차량 배정 제한 구현. 불가능하면 다른 차량으로 옮기지 않고 미처리 |
| 결과 차이 | OK/TIMEOUT/FAILED, seq·대문자 action·UTC eta·원본 ID·미처리 priority/reason으로 변환 |
| 공통에 없는 결과 필드 | 거리·기간·별도 처리 상태를 wire에 추가하지 않고 코어에서 유지 |

차고지·스냅샷 기준 시각은 SolveContext, 도로 공급자는 travel_time으로 호출자가 명시한다.
Kafka 실행기가 권역·스냅샷 메타데이터에서 이 설정을 얻는 연결은 남아 있다.
독립 worker는 Kafka 결과 발행·offset commit과 단일 상태 볼륨의 파일 중복 방지를 구현했다.
발행 직후 장애에서는 같은 결과 eventId가 재전달될 수 있어 backend 소비자 중복 제거도 필요하다.
retry/DLQ·운영 상태 저장·배포 정책은 담당자와 연결해야 한다. 순수 계산 함수 자체는 저장하지 않는다.

테스트는 공통 YAML을 직접 읽어 정상·부분 처리·오류·타임아웃 결과를 검증한다.
실제 도로 품질·서버 배포·전체 응답시간 완료를 의미하지 않는다.

## 최신 명세 반영

공통 문서 버전은 올라갔지만 Envelope·SolveStation·SolveTruck·SolveRequest·SolveResult와
공개 Route·Stop·Unserved 모델은 이전 검토와 동일하다. 입력·결과 변환 동작은 유지한다.
내부 SolveRequest·PlanCandidate도 동일하다. 이번 main의 현장 보고·내부 인증·감사 로그와
ai.events 재시도/DLQ는 backend 변경이며 solve.* 실행기에 재시도/DLQ가 추가된 것은 아니다.
경로 변경 승인 순서는 backend의 requiredApprovers를 따르며 optimizer가 결정하지 않는다.
작업 threadId는 backend가 정하는 작업 단위 값이며 optimizer의 메시지 키는 계속 shiftId다.
새 TaskEvent.code를 SolveResult에 추가하지 않는다.
작업 이벤트 seq도 backend가 부여하므로 SolveResult에 추가하지 않는다.

최신 공개 API v0.8의 로그인 제한 설명 변경은 solve·Route·Stop·Unserved 구조를 바꾸지 않는다.
명세·예시와 역할 5 구현·인프라는 같은 저장소에 있으며, [실행 안내](README.md)도 상대 경로로 연결한다.
