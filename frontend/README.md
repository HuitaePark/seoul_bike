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
| `src/shared/composables/useTaskStream.ts` | `useTaskStream(taskIdRef)` → `answer`, `activeTool`, `pendingApproval`, `sources`, `error`, `state`. 끊기면 `Last-Event-ID`로 다시 붙는다 |
| `src/features/task-console/` | 관제 작업 화면 (1): 작업 종류·권역·날짜·근무조 선택 → 작업 만들기 → 진행 이벤트 로그 → 승인 카드 → 근거. 권역 선택기는 현재 광진·성동만 활성화한다 |
| `src/features/approval/` | 승인 카드 (1). 기사 화면도 `<ApprovalCard :card :error @decide>`로 쓴다. 버튼은 늘 보이고, 승인 주체가 아니면 backend의 403 `NOT_APPROVER`를 `error`로 보여 준다 |
| `src/features/dashboard/` | 관제 대시보드 (`/dashboard`, MANAGER 첫 화면): 선택형 KPI와 오른쪽 상세 아코디언, 위험 거점 지도(SVG 개략도), 변경안 전후 경로 미리보기, 지도 아래 트럭별 경로·적재량, 포기 거점, 질의. **더미 데이터** |
| `src/features/driver/` | 기사 앱 (`/driver`, DRIVER 첫 화면, 모바일 틀): 다음 정류장, 길안내, 수량 +/- 보고 시트, 현장 사정, 변경 제안(운행 중엔 배지만). **더미 데이터** |
| `src/features/report/` | 성과 리포트 (`/report`, EXECUTIVE 첫 화면): 날짜별 정책 4종 지표 비교. **더미 데이터** |
| `src/shared/dummy.ts` | 대시보드·기사 앱이 같이 쓰는 더미 근무조 (계약 타입 그대로). API를 붙이면 지운다 |

## 규칙

- 화면은 backend `/api/**`만 호출한다. AI 서버 주소를 쓰지 않는다 (과제 금지 사항).
- 권한 판단은 backend가 한다. 화면은 역할에 따라 **보여줄 것만** 고르고, 403은 메시지로 보여준다.
- 새 화면은 `src/router.ts`에 경로를 추가한다. 로그인이 필요 없는 화면만 `meta: { public: true }`. 상단 바 없이 모바일 틀을 쓰는 화면은 `meta: { layout: 'mobile' }`.
- `/`는 역할별 첫 화면으로 보낸다 (관리자 `/dashboard`, 기사 `/driver`, 책임자 `/report`). 작업 콘솔은 `/console`.

## 계획 검토 화면 (역할 5)

`/plans/review/demo`는 로그인 없이 지도·마커·트럭 방문 순서·적재량·미처리 사유·거점 제외와 새 후보 승인 흐름을 시연한다.
`/plans/review`는 관리자·책임자의 실제 조회 화면이며 실제 업무 API 연결은 후속이다.
MapLibre·OpenFreeMap을 연결했다. 점선은 방문 순서이고 실제 도로 경로는 후속이다.
[상세 실행·검증 안내](src/features/plan-map/README.md)를 참고한다.
검사는 `npm test`, `npm run build`, Chromium 설치 후 `npm run test:e2e`로 실행한다.

## 시연 계정

`manager`(관제), `driver1`·`driver2`(기사, truck-1·truck-2), `executive`(책임자). 비밀번호는 서버 `.env`의 `SEED_USER_PASSWORD`이며 팀에 따로 공유한다.

진짜 agent가 붙기 전까지는 backend의 **가짜 에이전트**가 답한다. 모든 답에 `[가짜 에이전트]`가 붙는다.
