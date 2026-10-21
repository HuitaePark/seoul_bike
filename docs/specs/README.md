# 계약 (초안 v0.1)

팀이 서로 맞춰야 하는 인터페이스의 원본이다. 코드보다 이 파일을 먼저 고친다.

| 파일 | 내용 | 쓰는 쪽 → 받는 쪽 | 담당 |
| --- | --- | --- | --- |
| [`openapi.yaml`](openapi.yaml) | 공개 API: 로그인, 작업 API 5개(202·SSE·승인), 대여소, 계획, 현장 보고, 성과 | 화면 → backend | 백엔드 |
| [`internal-api.yaml`](internal-api.yaml) | 내부 API `/internal/**`, MCP 도구 ↔ API 대응표, 승인 확인 규칙 | business-mcp → backend | 백엔드 + MCP |
| [`asyncapi.yaml`](asyncapi.yaml) | Kafka 토픽과 메시지: `ai.tasks`, `ai.events`, `solve.requests`, `solve.results` | backend ↔ agent, backend ↔ optimizer | 백엔드 + 에이전트 + 최적화 |

## 보는 법

- **배포 서버 Swagger:** https://baki-macbookpro.tail3286a4.ts.net/swagger (공개 API, 내부 API를 위쪽 선택 상자로 전환)
- **서버에 접속이 안 될 때:** [Swagger Editor](https://editor-next.swagger.io/?url=https://raw.githubusercontent.com/HuitaePark/skala_seoul_bike/main/docs/specs/openapi.yaml) · [AsyncAPI Studio](https://studio.asyncapi.com/?url=https://raw.githubusercontent.com/HuitaePark/skala_seoul_bike/main/docs/specs/asyncapi.yaml)
- **로컬 검증:**
  ```bash
  npx @redocly/cli lint docs/specs/openapi.yaml docs/specs/internal-api.yaml
  (cd docs/specs && npx @asyncapi/cli validate asyncapi.yaml)
  ```
  CI(`contracts` 잡)도 PR마다 같은 검증을 돌린다.

## 바꿀 때

1. 필드는 **추가만** 한다. 지우거나 이름을 바꿔야 하면 쓰는 쪽 담당과 먼저 합의한다.
2. `info.version`을 올리고 PR 설명에 무엇이 바뀌었는지 적는다.
3. 계약 PR은 쓰는 쪽과 받는 쪽 담당이 함께 승인한다.

## 구현 상태

| 계약 | 상태 |
| --- | --- |
| 로그인 · `/api/me` · JWT · 역할 3개 | 구현 |
| 회원가입(승인 대기) · 관리자 계정 관리 (`/api/admin/users`) | 구현 (v0.2) |
| 작업 API 5개 (202 · SSE · `Last-Event-ID` 재전송 · 승인) | 구현 |
| `ai.tasks` 발행 · `ai.events` 소비 (`seq`는 backend가 부여) | 구현. agent 자리는 **가짜 에이전트**(`FAKE_AGENT_ENABLED`)가 대신한다. `ai.events` 소비는 **재시도(`ai.events.retry`) → `ai.events.dlq`** (형식 오류는 바로 dlq) |
| `/api/system/status` | 구현 |
| 현장 보고 `POST /api/reports` (기사만, `issue`가 있으면 재경로 작업) | 구현 (v0.5). 정류장이 기사 경로에 있는지는 경로가 생기면(M3) 확인 |
| 대여소 · 계획 · 성과 API | 계약만 |
| `/internal/**` 서비스 토큰(`X-Internal-Token`) · 현장 보고 조회(`get_field_reports`) · 제약 추가(`add_constraint`) · 감사 기록(`get_audit`) | 구현 (internal-api v0.5) |
| `solve.requests` 발행 · `solve.results` 소비 · 계획 후보 저장 | 구현 (backend 안쪽). `requestId`(`{shiftId}:{planVersion}:{seq}`)와 요청·결과를 `solve_requests`에, OK 결과는 `plans`에 후보(`PROPOSED`)로 남기고 활성 계획과 비교한 `diffs`·`grade`·`requiredApprovers`를 붙인다. 같은 결과가 두 번 와도 후보는 하나. `app.solve.timeout`(60초)을 넘기면 기다림을 그만두고, 늦은 결과는 요청 기록에만 남는다. `solve.results` 소비는 **재시도(`solve.results.retry`) → `solve.results.dlq`** (형식 오류는 바로 dlq) |
| 나머지 `/internal/**`(대여소·예측·계획·트럭·경로 계산·계획 활성화·경로 적용) | 계약만. 경로 계산 `/internal/plans/solve`는 위 왕복을 부르기만 하면 되지만, 입력(대여소 `neededBikes`·`priority`, 트럭 위치·적재)을 만들 대여소 데이터(PR #10)를 기다린다. 시간 초과 임시안(`provisional`)도 그때 |
| 변경 등급 규칙 · 경로 변경 승인 순서 확인 | 구현 (등급 기준은 `application.properties`의 `app.route-change.*`). 확인 규칙을 부를 적용 API `/internal/routes/{truckId}/apply`는 `/internal`과 함께 M3. 그전까지는 가짜 에이전트가 같은 확인 규칙을 부른다 |

## 이 초안이 정한 것

- 작업 API 경로와 이름은 과제 레퍼런스(표 5-8)를 그대로 따른다.
- 진행 이벤트는 `TOKEN`, `TOOL_START`, `TOOL_END`, `APPROVAL_REQUIRED`, `DONE`, `FAILED` 여섯 가지다. SSE `data`와 Kafka `ai.events` 메시지는 같은 형식이다.
- **변경 API 3개(`/internal/constraints`, `/plans/{id}/activate`, `/routes/{truckId}/apply`)는 필요한 승인이 모두 `APPROVED`가 아니면 403**을 돌려준다. 에이전트 interrupt와 별개로 backend가 한 번 더 막는다.
- **`propose_route_change`의 도구 앞 승인이 곧 기사 수락이다** (HARNESS.md 10절 (나)). 따로 수락 흐름을 두지 않아 같은 변경을 두 번 누르지 않는다. 작은 변경(MINOR)은 기사 카드 한 장, 큰 변경(MAJOR)은 관리자 → 기사 카드 두 장을 같은 작업에서 순서대로 보낸다. 누구에게 보낼지는 backend가 후보의 `requiredApprovers`로 정하고, 에이전트는 등급으로 판단하지 않는다.
- Kafka 메시지 키는 `shiftId`, 모든 메시지에 `eventId`(중복 제거), `traceId`(관측)를 넣는다. 실패하면 `<토픽>.retry` → `<토픽>.dlq`.
- LangGraph `thread_id`는 **작업 단위 `{shiftId}:{taskId}`**다. backend가 `TaskCommand.threadId`로 보내고 agent는 직접 만들지 않고 그대로 쓴다. 근무조 상태(계획)의 원본은 DB다. (근무조 단위로 쓰면 같은 근무조 작업끼리 대기 중인 승인이 섞인다. HARNESS ISS-03)
- 기사 작업은 `requestedBy.truckId`로 그 트럭에 한정한다 (NFR-06). backend는 JWT의 트럭으로 채운다.
- SSE로는 계약 `TaskEvent` 필드만 보낸다. agent의 `harness` 같은 확장 필드는 저장·전달하지 않는다.
- **감사 기록(`audit_events`)은 append-only**다 (DB 트리거가 수정·삭제·TRUNCATE를 막는다). 승인·거절·되돌림과 제약 추가는 그 변경과 같은 트랜잭션에서, 현장 보고는 보고가 확정되는 마지막 단계(저장 또는 재경로 작업 연결)와 같은 트랜잭션에서 남긴다. 사람 이름 대신 "관리자", "truck-1 기사"로 가린다. FR-18 "무승인 변경 0건"의 증거다.
- **현장 보고 작업(FIELD_REPORT)은 기사의 `POST /api/reports`로만 생긴다** (`issue`가 있을 때). 작업 API로는 만들지 않는다(400 `USE_REPORTS_API`). 명령에 `reportId`와 보고 값 `report`를 싣는다 (AGENT-GRAPH G13·G2).

## 확인이 필요한 것 (담당자에게)

| 질문 | 누구 |
| --- | --- |
| MCP 도구 10개 이름·인자가 이 대응표와 맞는가 | MCP |
| `TaskCommand`·`TaskEvent` 필드로 LangGraph 시작·재개·이벤트 발행이 충분한가 | 에이전트 |
| `SolveRequest`·`SolveResult`가 OR-Tools 입력·출력과 맞는가 (`neededBikes` = 기존 `Imbalance.delta`) | 최적화 |
| 화면에 필요한 필드가 빠지지 않았는가 (승인 카드, 근거 패널, 지도) | 화면 담당 전원 |
| PRD 6.1의 "근무조 하나 = 그래프 스레드 하나"를 작업 단위 스레드(`{shiftId}:{taskId}`)로 고칠 것 | 기획 리드 |
