# 역할 5 — 후속 PR 범위와 마무리 준비 (2026-10-21)

기준 main은 `b31fcec`(#89·#90·#92·#88·#93)이며 브랜치는 `hyunmoo`다.
기존 [PR #90](https://github.com/HuitaePark/skala_seoul_bike/pull/90)은 이전 head `0c1d529`로 병합됐다.
이번 PR은 그 병합에 포함되지 않은 #89 호환 검사/문서와 [합의사항](../data/agreements.md)의 역할 5 추가 대응을 묶는다.
공개 API 0.13.1·내부 API 0.8.3·Kafka 0.5.1-draft·payload schemaVersion 1을 유지한다.

## 제출 범위

- #89 생산자 입력의 PLAN·비정시 EVE REPLAN·21:00 종료·차단/고정 ID·priority 보존·빈 계산 결과를 양 정책으로 검증한다.
  #92의 트럭 상태 조회와 현재 위치/적재 계산도 같은 계약이다. 후보의 빈 트럭/과거 seq/startLoad 병합은 backend가 담당한다.
- 새 SolveResult occurredAt/eta는 합의 4의 +09:00으로 보낸다. 다른 입력 오프셋도 수용하며 저장된 결과·UTC 로그는 유지한다.
- 지도 공개 시연은 실제 N02 ST-ID·이름·좌표·거치대·고도·역할과 TR-03~TR-05를 사용한다.
  재고/예측/위험/계획은 가상으로 구분한다. 화면은 실제 현황이 없는 미처리 거점의 위치를 만들지 않는다.
- 정시 로컬 시간별 목표 검사를 `inventory_policy="hourly"`/`--inventory-policy hourly`로 제공한다.
  기본 endpoint와 운영 backend StationRisk의 neededBikes/priority 계산을 유지한다.
- 선택형 OSRM Compose overlay는 읽기 전용 도로 파일의 data-version을 worker/행렬 캐시에 연결하고 기존 상태 볼륨을 보존한다.
  기본 배포는 하버사인 그대로이며 새 CI 잡 없이 기존 Compose 검사에 overlay 검증만 추가한다.
- [마무리 인계](final-handoff.md)에 실제 입력/담당자/입수처·다음 실행 순서·완료 기준을 정리한다.

## 확인 결과

- 최적화 392 passed·24 subtest. 처음 제외된 실제 Kafka 검사는 임시 독립 브로커에서 별도로 3 passed이며 환경을 정리했다.
- 데이터/시뮬레이터/분석 44 passed, 실자료가 필요한 1개 제외.
- 프론트 단위 37 passed·Chromium 28 passed·빌드 통과.
- 기존 세 Compose와 새 overlay 구성 검증, 상태/캐시 볼륨·도로 경로·기동 순서·내부 포트 결합 확인.
- 실제 OSRM PBF 전처리/컨테이너·업무 서버 전체 통합·전체 10초와 backend Java 25 로컬 실행은 미검증이다.
  이번 PR CI 결과는 제출 뒤 별도 확인한다. 과거 #90 CI를 이번 head의 결과로 사용하지 않는다.

## 다음 작업에 필요한 것

| 항목 | 준비 담당 | 역할 5의 다음 완료 확인 |
| --- | --- | --- |
| 실제 관측·시간별 예측·미종료 근무조/트럭/차고지 | 역할 4·6, 내부 API 입력과 DB 적재 | 실제 neededBikes→worker→후보 저장, 승인/활성화/기사 반영 |
| 17구역·차고지/연결도로 PBF와 출처·취득일·해시 | 역할 5/서버 담당 | CH 전처리·단절/캐시 검사, N01/300곳 규모 측정 |
| RAM·디스크·CPU/Compose와 운영 테스트 환경 | 서버 담당·역할 4 | 전체 기동·재시작/볼륨 복구·현장 보고→변경안 10초 |
| 03a·03b/장애 시나리오와 추적 기록 | 역할 1·2·4·6 | 실제 지도 결정·변경 수락·중복/retry/DLQ 복구 |

작업 중 [PR #88](https://github.com/HuitaePark/skala_seoul_bike/pull/88)이 main e125e6e에 병합되어
MCP·관제 기본값·공통 구역 목록의 N02 이전을 함께 반영했다. 역할 2 평가용 가짜 데이터 이전은 담당자가 확인한다.
실시간 collector/배포 설정 [PR #93](https://github.com/HuitaePark/skala_seoul_bike/pull/93)도 작업 중 main b31fcec에 병합됐다.
합의 문서의 역할 4 최신 상태와 역할 5 추가 구현을 모두 보존해 병합했다. SEOUL_API_KEY의 production secret 등록·관측 갱신과 실제 예측 적재는 후속이다.
실제 도로선은 공개 geometry 응답 계약이 필요한 별도 확장이다. 로컬 중간 시간 목표 검사를 운영 관측 정렬과 동일하게 취급하지 않는다.
위 준비 조건이 충족돼야 다음 작업에서 운영 역할 마무리가 가능하다.
