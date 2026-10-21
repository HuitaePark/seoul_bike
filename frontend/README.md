# web (Vue 3)

관제 대시보드 · 기사 PWA · 성과 리포트를 한 앱에서 만든다. 화면은 각 담당이 `src/features/<기능>/`에 만든다.

## 실행

```bash
npm ci
npm run dev            # http://localhost:5173, /api는 backend(:8080)로 프록시
BACKEND_URL=https://baki-macbookpro.tail3286a4.ts.net npm run dev   # 배포 서버 backend에 붙이기
```

## 공통 뼈대

| 파일 | 쓰는 법 |
| --- | --- |
| `src/shared/api/client.ts` | `api.GET('/api/ai/tasks')`, `api.POST(...)`. 경로·요청·응답 타입이 계약에서 자동으로 맞춰진다. 토큰은 자동으로 붙는다 |
| `src/shared/api/schema.d.ts` | `docs/specs/openapi.yaml`에서 생성. **손으로 고치지 않는다.** 계약을 바꾸면 `npm run gen:api` |
| `src/shared/auth/store.ts` | `useAuth()` → `login`, `logout`, `user`, `token` |
| `src/shared/composables/useTaskStream.ts` | `useTaskStream(taskIdRef)` → `answer`, `activeTool`, `pendingApproval`, `sources`, `error`, `state`. 끊기면 `Last-Event-ID`로 다시 붙는다. 여러 작업을 함께 볼 때는 작업마다 `openTaskStream(taskId, { onEvent, onState })`를 열고 `close()`로 닫는다 |
| `src/features/task-console/` | 관제 작업 화면 (1): 작업 만들기 → 진행 이벤트 로그 → 승인 카드 → 근거 |
| `src/features/approval/` | 승인 카드 (1). 기사 화면도 `<ApprovalCard :card :error @decide>`로 쓴다. 버튼은 늘 보이고, 승인 주체가 아니면 backend의 403 `NOT_APPROVER`를 `error`로 보여 준다 |
| `src/features/dashboard/` | 관제 대시보드 (`/dashboard`, MANAGER 첫 화면): KPI, 위험 거점 지도(SVG 개략도), 대기 중 변경안, 거점 근거, 트럭별 경로, 포기 거점, 질의. 승인 대기 카드·질의 답·결정 결과는 작업 API(`GET /api/ai/tasks`)와 작업별 SSE로 실시간 반영한다. 대여소(`GET /api/regions/{regionId}/stations`), 계획(`GET /api/shifts/{shiftId}/plan`), 트럭, 무승인 변경 수(`GET /api/shifts/{shiftId}/safety`)는 주기적으로 읽는다(`useBoard`). 트럭 위치·적재는 트럭 상태 API(`GET /api/shifts/{shiftId}/trucks`, GPS가 아니라 마지막으로 끝낸 정류장, 없으면 차고지)다. 구역은 레일에서 고른다(처음 N02) |
| `src/features/driver/` | 기사 앱 (`/driver`, DRIVER 첫 화면, 모바일 틀): 다음 정류장, 길안내, 수량 +/- 보고 시트, 현장 사정, 변경 제안(운행 중엔 배지만). 자료는 `useDriver`: 경로 `GET /api/drivers/me/route`(`DriverRoute`, 근무조 포함), 구역 대여소(길찾기 좌표·앱 재고), 변경 제안(승인 대기 작업의 기사 카드), 보고 `POST /api/reports`(정류장 진행을 backend가 남긴다). 운행·정차는 기사가 누른다 |
| `src/features/report/` | 성과 리포트 (`/report`, EXECUTIVE 첫 화면): 날짜·구역별 정책 4종 지표 비교 (`GET /api/kpi`). backend 구현·시뮬레이터 결과가 없으면 "결과 없음" (ISS-25) |

## 규칙

- 화면은 backend `/api/**`만 호출한다. AI 서버 주소를 쓰지 않는다 (과제 금지 사항).
- 권한 판단은 backend가 한다. 화면은 역할에 따라 **보여줄 것만** 고르고, 403은 메시지로 보여준다.
- 새 화면은 `src/router.ts`에 경로를 추가한다. 로그인이 필요 없는 화면만 `meta: { public: true }`. 상단 바 없이 모바일 틀을 쓰는 화면은 `meta: { layout: 'mobile' }`.
- `/`는 역할별 첫 화면으로 보낸다 (관리자 `/dashboard`, 기사 `/driver`, 책임자 `/report`). 작업 콘솔은 `/console`.
- 색 그림자를 쓰지 않는다. 떨어지는 그림자(`box-shadow` 위치·번짐, `drop-shadow`)는 중립색(`rgb(15 23 42 / a)`·검정·흰색)이나 `var(--shadow)`만 쓴다. 선택·포커스 고리(`0 0 0 Npx`, `inset`)는 색을 써도 된다. `npm test`의 `tests/style-rules.test.ts`가 `src` 전체를 검사하고, 대시보드 e2e가 그려진 화면에서 한 번 더 본다.

## 계획 검토 화면 (역할 5)

`/plans/review/demo`는 로그인 없이 지도·마커·트럭 방문 순서·적재량·미처리 사유·거점 제외와 새 후보 승인 흐름을 시연한다.
`/plans/review`는 관리자·책임자의 실제 조회 화면이며 실제 업무 API 연결은 후속이다.
MapLibre·OpenFreeMap을 연결했다. 점선은 방문 순서이고 실제 도로 경로는 후속이다.
[상세 실행·검증 안내](src/features/plan-map/README.md)를 참고한다.
검사는 `npm test`, `npm run build`, Chromium 설치 후 `npm run test:e2e`로 실행한다.

## 시연 계정

`manager`(관제), `driver1`·`driver2`(기사, 시연 구역 N02의 TR-03·TR-04), `executive`(책임자). 비밀번호는 서버 `.env`의 `SEED_USER_PASSWORD`이며 팀에 따로 공유한다.

진짜 agent가 붙기 전까지는 backend의 **가짜 에이전트**가 답한다. 모든 답에 `[가짜 에이전트]`가 붙는다.
