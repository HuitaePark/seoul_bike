# 공통 solve 명세 대응 기록

기준: main e819a4b의 공통 v0.1. 공유 YAML을 유지하고 개인 브랜치의 코드·문서를 맞췄다.
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
영속 중복 제거·결과 발행·offset commit·retry/DLQ도 실행기 연결 단계에서 담당자와 구현한다.
현재 함수는 중복 결과를 저장하지 않는다.

테스트는 공통 YAML을 직접 읽어 정상·부분 처리·오류·타임아웃 결과를 검증한다.
실제 도로 품질·서버 배포·전체 응답시간 완료를 의미하지 않는다.
