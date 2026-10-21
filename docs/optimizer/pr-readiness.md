# 역할 5 — 현재 상태와 다음 PR 준비 (2026-10-21)

기준 main은 `9ea362f`(#105 트럭 공개 API·#103 화면 후속 포함)이며 브랜치는 `hyunmoo`다. 열린 역할 5 PR은 없다.
공개 API 0.14.0·내부 API 0.8.5·Kafka 0.5.1-draft·payload schemaVersion 1을 따른다.
[PR #96](https://github.com/HuitaePark/skala_seoul_bike/pull/96)으로 내부 API가 0.8.4가 됐다(`bikes`·`observedAt` 설명).
optimizer가 받는 계약 필드는 바뀌지 않으며, 현장 보고로 필요가 없어진 정류장이 REPLAN `stations`·`fixedStationIds`에서 빠진다.
#97은 가짜 에이전트·시드 기사를 N02(TR-03·TR-04)로 옮겨 역할 5 시연 트럭과 맞췄다. #99·#100은 시뮬레이터·관제 화면이며 역할 5 코드와 겹치지 않는다.

## 병합된 역할 5 PR

| PR | 범위 |
| --- | --- |
| [#29](https://github.com/HuitaePark/skala_seoul_bike/pull/29) | 최적화 코어·Kafka 실행기·검증 인프라·지도 계획 검토 |
| [#90](https://github.com/HuitaePark/skala_seoul_bike/pull/90) | 예측·운행 합의, 지도, 17구역 규모 검증 |
| [#94](https://github.com/HuitaePark/skala_seoul_bike/pull/94) | #89 solve 입력 호환, 결과 시각 +09:00, N02 실제 기준정보 시연, 로컬 시간별 검사, OSRM 선택 overlay, [마무리 인계](final-handoff.md) |
| [#98](https://github.com/HuitaePark/skala_seoul_bike/pull/98) | #94 리뷰 후속(OSRM 배포·env 주의, 시연 ST-2308, 시드 검사 완화), 도로 자료 포함 범위 검사 `rebalancing.roadcheck`, Python 3.11 CLI 상호 배타 인자 |

#98 기준 검증: 최적화 399 passed·24 subtests(실제 broker 3개 skipped), 프론트 단위 37·Chromium 28 passed. 상세는 [검증 기록](validation.md).

## 지금 남은 독립 작업

- **#96 대응 완료(hyunmoo, PR 전):** 현장 보고로 줄어든 REPLAN 입력을 계약 검사 `test_backend96_*` 4개(두 정책)로 확인했다.
  보고된 정류장이 빠지고 다른 트럭의 고정 작업이 남는 경우, 모든 필요가 보고로 사라진 적재 트럭의 빈 입력.
  optimizer 코드 변경은 필요 없었다. 최적화 403 passed·24 subtests(실제 broker 3개 skipped).
- **OSRM 영속 경로 대응(hyunmoo, PR 전):** 준비용 `OSRM_INPUT_DIR`·공통 `OSRM_DATA_DIR`로 저장소 밖 절대 경로를 지정한다.
  같은 전처리 결과를 준비 컨테이너만 쓰고 OSRM/worker는 읽기 전용으로 공유한다. 기존 상태/캐시 볼륨을 유지한다.
  기본 경로와 외부 공백·한글 경로를 실제 Compose config로 검증했다. 최적화 407 passed·24 subtests(실제 broker 3개 skipped).
  실제 도로 준비·배포 전환은 아래 조건이 필요하다.
- #105의 공개 TruckState에는 atStationId가 추가됐다. 위치는 마지막 DONE 정류장/차고지이며 GPS가 아니다.
  optimizer wire 필드는 바뀌지 않았다. 관제 대시보드의 API 연결은 역할 1이 담당한다.
- [PR #107](https://github.com/HuitaePark/skala_seoul_bike/pull/107)의 무중단 배포는 아직 main 밖의 변경이다.
  그 Compose에 대해서도 OSRM 외부 경로·기존 worker 상태 결합을 확인한다. 배포 워크플로는 이 브랜치에서 바꾸지 않는다.

## 다음 작업에 필요한 것

| 항목 | 준비 담당 | 역할 5의 다음 완료 확인 |
| --- | --- | --- |
| 실제 관측·시간별 예측·미종료 근무조/트럭/차고지 | 역할 6(예측 적재·`SEOUL_API_KEY`·collector 배포), 역할 4(내부 API) | 실제 neededBikes→worker→후보 저장, 승인/활성화/기사 반영 |
| 17구역·차고지/연결도로 PBF와 출처·취득일·해시 | 역할 5/서버 담당 | CH 전처리, `roadcheck`(실제 `--depot`·300곳), N01/300곳 cold/warm 측정 |
| 배포 OSRM 스위치·영속 경로 전달 | 역할 4·서버 담당 | hyunmoo에 준비한 OSRM_DATA_DIR/OSRM_INPUT_DIR의 production 변수 전달과 외부 자료 위치·배포 후 OSRM 유지 확인 |
| RAM·디스크·CPU/Compose와 운영 테스트 환경 | 서버 담당·역할 4 | 전체 기동·재시작/볼륨 복구·현장 보고→변경안 10초 |
| 03a·03b/장애 시나리오와 추적 기록 | 역할 1·2·4·6, [이슈 #91](https://github.com/HuitaePark/skala_seoul_bike/issues/91) | 실제 지도 결정·변경 수락·중복/retry/DLQ 복구 |

실제 도로선은 공개 geometry 응답 계약이 필요한 별도 확장이다. 로컬 중간 시간 목표 검사를 운영 관측 정렬과 동일하게 취급하지 않는다.
03a 실제 LLM의 승인 단계 누락 [#102](https://github.com/HuitaePark/skala_seoul_bike/issues/102)는 역할 1에서 해결·재검증한다.
실행 순서와 완료 기준은 [마무리 인계](final-handoff.md)를 따른다. 실제 입력/측정 증거 없이 운영 완료로 표시하지 않는다.
