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
| `src/features/task-console/` | 관제 작업 화면 (1): 작업 만들기 → 진행 이벤트 로그 → 승인 카드 → 근거 |
| `src/features/approval/` | 승인 카드 (1). 기사 화면도 `<ApprovalCard :card :error @decide>`로 쓴다. 버튼은 늘 보이고, 승인 주체가 아니면 backend의 403 `NOT_APPROVER`를 `error`로 보여 준다 |

## 규칙

- 화면은 backend `/api/**`만 호출한다. AI 서버 주소를 쓰지 않는다 (과제 금지 사항).
- 권한 판단은 backend가 한다. 화면은 역할에 따라 **보여줄 것만** 고르고, 403은 메시지로 보여준다.
- 새 화면은 `src/router.ts`에 경로를 추가한다. 로그인이 필요 없는 화면만 `meta: { public: true }`.

## 시연 계정

`manager`(관제), `driver1`·`driver2`(기사, truck-1·truck-2), `executive`(책임자). 비밀번호는 서버 `.env`의 `SEED_USER_PASSWORD`이며 팀에 따로 공유한다.

진짜 agent가 붙기 전까지는 backend의 **가짜 에이전트**가 답한다. 모든 답에 `[가짜 에이전트]`가 붙는다.
