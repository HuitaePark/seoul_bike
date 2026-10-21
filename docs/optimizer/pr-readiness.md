# 역할 5 — 현재 상태와 다음 PR 준비 (2026-10-21)

기준 main은 `a5fac87`(#98 병합)이며 브랜치는 `hyunmoo`다. 열린 역할 5 PR은 없다.
공개 API 0.13.1·내부 API 0.8.3·Kafka 0.5.1-draft·payload schemaVersion 1을 따른다.
열린 [PR #96](https://github.com/HuitaePark/skala_seoul_bike/pull/96)이 병합되면 내부 API가 0.8.4가 된다(`bikes`·`observedAt` 설명).
optimizer가 받는 계약 필드는 바뀌지 않으며, 현장 보고로 필요가 없어진 정류장이 REPLAN `stations`·`fixedStationIds`에서 빠진다.

## 병합된 역할 5 PR

| PR | 범위 |
| --- | --- |
| [#29](https://github.com/HuitaePark/skala_seoul_bike/pull/29) | 최적화 코어·Kafka 실행기·검증 인프라·지도 계획 검토 |
| [#90](https://github.com/HuitaePark/skala_seoul_bike/pull/90) | 예측·운행 합의, 지도, 17구역 규모 검증 |
| [#94](https://github.com/HuitaePark/skala_seoul_bike/pull/94) | #89 solve 입력 호환, 결과 시각 +09:00, N02 실제 기준정보 시연, 로컬 시간별 검사, OSRM 선택 overlay, [마무리 인계](final-handoff.md) |
| [#98](https://github.com/HuitaePark/skala_seoul_bike/pull/98) | #94 리뷰 후속(OSRM 배포·env 주의, 시연 ST-2308, 시드 검사 완화), 도로 자료 포함 범위 검사 `rebalancing.roadcheck`, Python 3.11 CLI 상호 배타 인자 |

#98 기준 검증: 최적화 399 passed·24 subtests(실제 broker 3개 skipped), 프론트 단위 37·Chromium 28 passed. 상세는 [검증 기록](validation.md).

## 지금 남은 독립 작업

- #96 병합 뒤: 현장 보고로 줄어든 REPLAN 입력(빈 `stations`, 줄어든 `fixedStationIds`)을 기존 테스트 범위에서 확인한다.
  계약과 `PlanSolver`는 바뀌지 않아 코드 변경은 예상하지 않는다.
- 그 밖의 독립 구현은 남아 있지 않다. 아래 항목은 입력이 와야 진행한다.

## 다음 작업에 필요한 것

| 항목 | 준비 담당 | 역할 5의 다음 완료 확인 |
| --- | --- | --- |
| 실제 관측·시간별 예측·미종료 근무조/트럭/차고지 | 역할 6(예측 적재·`SEOUL_API_KEY`·collector 배포), 역할 4(내부 API) | 실제 neededBikes→worker→후보 저장, 승인/활성화/기사 반영 |
| 17구역·차고지/연결도로 PBF와 출처·취득일·해시 | 역할 5/서버 담당 | CH 전처리, `roadcheck`(실제 `--depot`·300곳), N01/300곳 cold/warm 측정 |
| 배포 OSRM 스위치와 도로 자료 보관 경로 | 역할 4 | runner 작업 폴더 밖 경로로 배포 후에도 OSRM 유지 확인 |
| RAM·디스크·CPU/Compose와 운영 테스트 환경 | 서버 담당·역할 4 | 전체 기동·재시작/볼륨 복구·현장 보고→변경안 10초 |
| 03a·03b/장애 시나리오와 추적 기록 | 역할 1·2·4·6, [이슈 #91](https://github.com/HuitaePark/skala_seoul_bike/issues/91) | 실제 지도 결정·변경 수락·중복/retry/DLQ 복구 |

실제 도로선은 공개 geometry 응답 계약이 필요한 별도 확장이다. 로컬 중간 시간 목표 검사를 운영 관측 정렬과 동일하게 취급하지 않는다.
실행 순서와 완료 기준은 [마무리 인계](final-handoff.md)를 따른다. 실제 입력/측정 증거 없이 운영 완료로 표시하지 않는다.
