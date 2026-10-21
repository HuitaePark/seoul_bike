# 지도·계획 검토 화면 명세 — 지도 서비스 제외

담당: 역할 5. 기준: main `94d86d1`, [공개 API v0.5.0-draft](openapi.yaml).
구현은 개인 브랜치 `feat/optimizer-core`에 있다. 공개 API 스키마를 확장하지 않는다.
지도 공급자 연결과 실제 업무 서버 통합은 이 화면의 단독 완료 범위에서 제외한다.

## 경로와 권한 표시

| 경로 | 동작 |
| --- | --- |
| `/plans/review` | 로그인 필요. MANAGER는 조회·계획 결정, EXECUTIVE는 조회만, DRIVER는 전체 계획 요청 없이 안내 |
| `/plans/review/demo` | 공개 시연. 별도 가상 자료·승인 상태만 변경하며 실제 API 호출 없음 |

권역 ID·근무조 ID·예측 구간 1~6시간을 입력한다. URL의 `regionId`, `shiftId`로 초기 조회할 수 있다.
권한의 최종 판정은 backend가 담당한다. 401은 공통 인증 처리로 로그인 화면에 이동한다.

## 표시 항목

| 영역 | 공개 응답 필드·동작 |
| --- | --- |
| 요약 | `Plan.planId/shiftId/version/status/provisional/explanation`, 트럭·작업·미처리 개수 |
| 트럭 선택 | `Route.truckId/capacity/startLoad`, 회수/배치 작업 대수 합·완료/대기 개수, 빈 경로 |
| 방문 순서 | `Stop.seq/stationId/stationName/action/bikes/loadAfter/eta/status`, 적재 범위 확인 |
| 거점 상세 | 현재/예상 재고, 거치대, 관측 시각, 위험·역할·고도, `Stop.reason` |
| 미처리 | `Unserved.stationId/stationName/priority/reason`, 우선순위 내림차순 |
| 권역 현황 | 이름·ID 검색, 위험·역할 필터, 현재/예상 재고·관측 시각 |

선택 필드가 없으면 `미제공`으로 표시하고 용량 15·적재량 0을 추정해 넣지 않는다.
예측 재고의 0·음수를 보존한다. 우선순위는 미처리 응답에서만 제공되므로 다른 거점에는 만들지 않는다.
예상 빈 대여소 시각과 적용된 제약은 현재 공개 응답에 없어 `미제공`이다.
미처리 코드 `not_selected`를 시간 부족·용량 부족 하나로 단정하지 않는다. 모르는 코드는 원문을 표시한다.
이름이 없으면 현황의 이름, 그마저 없으면 원본 ID를 사용한다. 모든 시각은 한국 시간이다.

## API와 결정 흐름

| API | 사용 |
| --- | --- |
| `GET /api/shifts/{shiftId}/plan` | 계획 조회. 404는 계획 없음 |
| `GET /api/regions/{regionId}/stations?horizonH=` | 대여소 현황. 실패해도 계획은 별도로 표시 |
| `GET /api/ai/tasks/{taskId}` | 입력한 작업 UUID의 최신 상태·승인 카드 확인 |
| `GET /api/ai/tasks/{taskId}/stream` | 공통 `useTaskStream`으로 수신. 승인/도구 완료/종료 이벤트 때 계획·작업 재조회 |
| `POST /api/ai/tasks/{taskId}/approval` | 최종 승인·거절·거점 제외 요청. `/internal` 직접 호출 없음 |

결정 전 표시 조건은 MANAGER, 계획 DRAFT/PROPOSED, 동일 근무조,
WAITING_APPROVAL, SUBMIT_PLAN/MANAGER 카드, 카드와 현재 계획 ID 일치다.
조회 조건이 바뀌었거나 최신 조회 중이면 결정하지 않는다. 실제 유효성·권한은 서버가 다시 검사한다.

최종 승인은 현재 버전 검토 확인 후 `approvalId + APPROVE`, 거절은 `REJECT`를 전송한다.
의견은 선택이며 500자까지다. 대기 거점만 제외할 수 있고 ID를 중복 없이 전송한다.
제외 선택 시 최종 승인을 막고 `APPROVE + excludeStationIds`로 새 후보를 요청한다.
새 계획 ID·버전 또는 승인 ID가 바뀌면 검토 확인·제외 목록·의견을 초기화한다.
새 후보는 자동 확정하지 않으며 다시 검토·최종 승인해야 한다.

202는 결정 접수다. 화면이 ACTIVE를 만들지 않고 재조회 결과를 따른다.
중복 클릭과 응답 유실 시 동일 승인 ID를 재전송하지 않으며, 409에서는 오래된 요청 안내와 최신 상태를 표시한다.
계획 생성·경로 변경 승인 카드는 다른 담당 모듈의 흐름을 유지한다.

## 완료·검증 범위

단독 화면, 계약 기반 API 어댑터·가상 시연, 빈 결과·현황 일부 실패·서버 오류·재시도,
조회 취소·늦은 응답 차단, 역할별 표시와 모바일 화면을 검증한다.
시연의 새 후보는 고정 가상 자료를 변환한 것으로 최적화 엔진의 품질·업무 서버 통합을 입증하지 않는다.
실제 지도·도로선, 실제 계획/대여소 API와 승인 후 활성화·재계산 통합은 담당자 연결 후 검증한다.

[실행·검증 안내](../../frontend/src/features/plan-map/README.md)
