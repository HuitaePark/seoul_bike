<!--
Author: Jung Minkyu
Date: 20OCT2026
Purpose: 1 에이전트 그래프 진행 상황 한 장 요약 (팀 공유용). 무엇이 끝났고, 무엇이 남았고, 어떻게 확인하는지.
-->

# 1 에이전트 그래프: 진행 상황과 확인 방법

| 항목 | 내용 |
| --- | --- |
| 기준 | 팀 main `a5fac87` (specs openapi 0.13.1 · asyncapi 0.5.1 · internal-api 0.8.3). 실제 서비스 03a는 4가 main `5381273`에서 확인 (#91) |
| 날짜 | 2026-10-21 |
| 자세한 설계 | [AGENT-GRAPH](../AGENT-GRAPH.md) (근거·결정·결과 전부) |

## 한 줄 결론

**내 파트(근무조 그래프, 승인 카드 화면)는 만들 수 있는 것을 다 만들었고, 03a는 팀 main의 실제 서비스로 끝까지 통과했다.**
4가 main `5381273`에서 backend·Redpanda·optimizer·business-mcp(`MCP_INTERNAL_MODE=http`)·rag-mcp·agent(`AGENT_LLM=none`, `MCP_FAKE=0`)로
근무조 `N02:2026-10-21:EVE`(N02 대여소 175곳)를 돌려 `E2E 03a OK`를 받았다 (작업 생성부터 DONE까지 약 1.05초, #91).
남은 것은 03b(현장 보고 → 경로 변경)를 팀 main에서 끝까지 누르는 것 하나다 (아래 3절 5단계).

---

## 1. 무엇을 만들었나

| 만든 것 | 하는 일 | 위치 |
| --- | --- | --- |
| 근무조 그래프 | backend가 Kafka `ai.tasks`로 보낸 작업을 받아 Harness(모델·도구)를 돌리고, 결과를 `ai.events`로 돌려준다. 승인 카드가 나오면 사람의 결정(RESUME)을 받아 이어서 실행한다 | `agent/graph/` |
| 모델 없이 도는 길 | 모델이 없거나 고장이어도 계획 초안·숫자뿐인 현장 보고는 코드가 처리한다 (NFR-03) | `agent/graph/codepath.py` |
| 승인 카드 화면 | 승인·거절·일부 제외. 큰 경로 변경은 "관리자 승인 → 기사 수락" 진행을 보여 준다 | `frontend/src/features/approval/` |
| 관제 작업 화면 | 요청 입력, 작업 목록, 진행 이벤트 로그, 승인 카드, 근거 | `frontend/src/features/task-console/` |
| agent 컨테이너 | `docker compose up` 한 번으로 backend·Kafka와 함께 뜬다 | `agent/Dockerfile`, `infra/compose.yaml` |
| 통합 확인 스크립트 | 실제 backend로 03a를 끝까지 돌려 본다 | `agent/e2e/check_03a.py` |

---

## 2. 끝났나?

### 2.1 ROLES 1 체크리스트

| 항목 | 상태 |
| --- | --- |
| 그래프: 계획 초안, 현장 보고·재경로, 상황 반영, 질의 | 완료 |
| MCP Client로 두 서버 연결 (`business_`, `rag_`) | 완료. main compose의 실제 business-mcp·rag-mcp로 03a를 돌렸다 (#91) |
| 모델이 도구를 고르고 근거와 함께 답함 | 완료 (Harness와 나눔). 실제 모델 `gemma4:31b`로 확인 |
| 변경 도구 앞 멈춤, 저장, 같은 스레드로 재개 | 완료. 프로세스를 다시 띄워도 이어진다 (큰 변경의 두 번째 카드까지) |
| `ai.tasks` 소비, `ai.events` 발행 | 완료. 실제 redpanda·backend로 확인 |
| 화면: 관제 작업 화면, 승인 카드 | 완료 |
| 금지: 업무 DB 직접 쓰기, 숫자를 모델이 만들기 | 지킴 |
| **완료 기준: 03a·03b가 실제로 돈다** | **03a는 팀 main에서 통과 (#91). 03b는 막는 것이 없어졌고 끝까지 눌러 볼 차례** |

### 2.2 다른 역할이 해야 끝나는 것

아래 넷은 모두 main에 들어왔다. 03b를 막던 것이 더는 없다.

| # | 무엇 | 누가 | 상태 |
| --- | --- | --- | --- |
| 1 | 현장 보고 API `POST /api/reports`, FIELD_REPORT 명령에 `reportId`·보고 값 | 4 | 들어옴 (G13, PR #18) |
| 2 | 내부 API `/internal/**` (M3) | 4 | 들어옴. 03a가 `MCP_INTERNAL_MODE=http`로 통과했다. 마지막으로 빠져 있던 트럭 상태 `GET /internal/shifts/{shiftId}/trucks`(MCP `get_truck_status`)도 `c5b064e`로 들어왔다 |
| 3 | MCP 서버를 main에 머지하고 compose에 등록 | 3 (5와 함께) | 들어옴. compose의 agent는 기본이 실제 MCP(`MCP_FAKE=0`)이고, 03a를 실제 business-mcp·rag-mcp로 통과했다 (#91) |
| 4 | Harness 테스트 1개 수정 | 2 | 고쳐짐. agent 전체 테스트가 `Ran 77 tests … OK (skipped=1)` |

### 2.3 팀이 정해 줄 것

| # | 내용 | 지금 처리 |
| --- | --- | --- |
| G17 | specs README "근무조 상태의 원본은 DB" — 그래프가 근무조 내부 기록(대기 카드·최근 보고)을 두어도 되나 | 둔다. 원본은 backend이고, 작업 사이 대체와 재시작 뒤 카드 단계에만 쓴다 |
| PR #10 합의 23번 | "근무조 ID를 thread_id로" | PR #9로 작업 단위 `{shiftId}:{taskId}`로 정해졌다. 6에게 고쳐 달라고 댓글을 남겼다 |
| PR #10 합의 1번 | 대여소 ID를 `"108"`로 | 2026-10-21 결정: 실시간 API ID `ST-숫자`로 정했다 ([ADR 0001](../adr/0001-station-id-is-realtime-api-id.md)). 지금 계약·Harness·MCP·화면의 `ST-` 가정은 그대로 맞다 |
| #91 참고 (4·5) | 차고지에 자전거 재고를 둘지. 지금은 트럭이 모두 빈 채로 출발해, 회수할 자전거(29대)보다 배치 수요(99대)가 많으면 트럭 한 대만 움직인다 | 4·5가 정한다. 그래프는 계획 카드 요약에 빈 트럭 수를 붙여 관리자가 카드에서 바로 보게 했다 |

---

## 3. 어떻게 확인하나

아래는 모두 저장소 루트에서 시작한다. 1단계만 해도 내 코드가 맞는지는 확인된다.

### 1단계: 단위 테스트 (1분, 키·Docker 필요 없음)

```bash
cd agent
uv pip install -e ".[dev]"
.venv/bin/python -m unittest tests.test_graph
```

**통과 기준:** 마지막 줄이 `OK` (그래프 36개). 가짜 MCP 서버(실제 MCP 프로토콜)와 정해진 순서로 답하는 모델로 03a·03b, 승인 순서, 재시작, 계약 스키마를 본다.

전체(Harness 포함)를 보려면 `.venv/bin/python -m unittest discover tests`. `Ran 77 tests … OK (skipped=1)`가 정상이다. 건너뛴 1개는 PostgreSQL 주소가 없을 때의 재개 테스트다.

### 2단계: 실제 backend와 붙여 보기 (10분, Docker 필요)

1. `infra/.env.example`을 `~/e2e.env`로 복사하고 채운다: `FAKE_AGENT_ENABLED=false`, `AGENT_LLM=none`, `MCP_FAKE=0`(기본), `MCP_INTERNAL_MODE=http`, `INTERNAL_TOKEN`(아무 값이나, backend·business-mcp가 같은 값을 읽는다), `SEOUL_API_KEY`(collector가 실시간 재고를 넣는다), `JWT_SECRET`(32바이트 이상), `SEED_USER_PASSWORD`, `POSTGRES_PASSWORD`, `VECTOR_PASSWORD`.
2. 띄운다: `docker compose -p seoulbike-e2e -f infra/compose.yaml --env-file ~/e2e.env up -d --build --wait`
3. 준비됐는지: `docker compose -p seoulbike-e2e logs agent | grep "assigned partitions"`가 보이고, `logs collector`에 "대여소 N곳 저장"이 한 번 이상 찍히면 된다.
4. 03a를 끝까지: `cd agent && .venv/bin/python e2e/check_03a.py <SEED_USER_PASSWORD> <근무조 ID>`. 근무조는 `{구역}:{오늘 날짜}:{AM|PM|EVE}`, 예: `N02:2026-10-21:EVE`.
5. 끝나면: `docker compose -p seoulbike-e2e down -v`

**통과 기준:** 마지막 줄 `E2E 03a OK`. 그 사이에 이것들이 보인다: 계획 카드(요약 "계획 … 활성화: 트럭 n대, 대여소 m곳") → 대여소 하나 빼고 다시 계산 → 새 카드 → 옛 카드에 늦게 온 승인은 409 → 승인 → `submit_plan` 실행 → DONE → 다시 붙으면 이벤트 1~9 재전송.

MCP 없이 그래프만 보려면 `MCP_FAKE=1`로 띄우고 4에서 근무조를 뺀다. 그러면 가짜 MCP의 `yeongdeungpo:2026-10-24:PM`을 쓴다.

### 3단계: 화면으로 보기 (선택)

2단계 스택을 띄운 채로 `cd frontend && npm ci && npm run dev` → http://localhost:5173 → `manager` / `SEED_USER_PASSWORD`로 로그인. 근무조를 2단계 4에서 쓴 근무조로 바꾸고(가짜 MCP면 `yeongdeungpo:2026-10-24:PM`) "계획 초안"을 접수하면 승인 카드가 뜬다. "일부 제외"로 대여소를 눌러 빼고, 새 카드를 승인하면 완료와 근거가 보인다.

### 4단계: 실제 모델로 보기 (선택, Ollama 키 필요)

`~/e2e.env`에서 `AGENT_LLM=`(비움), `OLLAMA_API_KEY`를 채우고 `docker compose -p seoulbike-e2e -f infra/compose.yaml --env-file ~/e2e.env up -d agent`. 모델이 도구를 골라 카드를 만들고, 질의는 문서 근거와 함께 답한다. (모델이 승인 뒤에도 "승인 요청하였습니다"라고 쓰는 문구 문제가 있다. 프롬프트라 2의 몫이다.)

### 5단계: 팀 main에서 마지막 확인

- 03a, 실제 MCP·backend: **통과.** 2단계가 이제 실제 MCP 기준이고, 4가 main `5381273`에서 돌려 `E2E 03a OK`를 받았다 (#91).
- 03b, 남음: 2단계 스택에서 기사 계정으로 현장 보고를 보내 경로 변경 카드(작은 변경: 기사 한 장, 큰 변경: 관리자 → 기사)를 끝까지 누른다. 트럭 상태 API(`c5b064e`)까지 들어와 막는 것은 없다.
- 03b까지 통과하면 ROLES 1 완료 기준을 팀 main에서 채운 것이다.

---

## 4. 이번에 찾아 고친 문제

| 문제 | 영향 | 처리 |
| --- | --- | --- |
| MCP 도구 오류가 예외가 아니라 글로 돌아옴 (ISS-21) | backend가 거부한 변경을 "실행했다"고 보고했다 | tool call로 불러 오류를 실패로 가린다 |
| 관리자 승인 뒤 agent가 재시작되면 기사 카드가 풀리지 않음 (ISS-19) | 큰 변경이 영영 대기 | 카드 단계를 체크포인트에 남겨 되돌린다 |
| 승인자 의견이 가리지 않은 채 모델에게 감 (ISS-20) | 기사 이름·연락처 노출, 지시처럼 읽힘 | 가리고 `<user_input>`으로 감싼다 |
| rag-mcp 결과 모양을 Harness가 못 읽음 (ISS-23) | 모델 답변의 문서 근거가 통째로 빠짐 | 그래프가 문서 근거를 채운다 |
| 압축된 Kafka 메시지 하나에 agent가 계속 죽음 (ISS-22) | 재시작해도 같은 메시지에서 죽음 | 압축 해제 라이브러리 포함 |
| 계약 위반 명령(예: `reportId` 없는 보고) | 화면의 작업이 "대기"에 영원히 멈춤 | DLQ로 보내고 FAILED와 이유도 보낸다 |
| PostgreSQL 인코딩이 UTF8이 아님 | 재개가 오류 없이 저장되지 않음 | 체크포인터를 열 때 바로 멈춘다 |
| 계획 카드 요약이 계획 ID뿐 (#91) | 관리자가 트럭 몇 대·대여소 몇 곳인지 카드만 보고 모름 | 요약에 트럭·대여소 수와 빈 트럭 수를 붙인다. 대여소 수는 business-mcp가 자르기 전 수(`totalStops`)로 센다 |
| 계획 카드에 정류장 `eta`·이름이 없음 (#91) | 카드에서 도착 시각을 못 봄 | 원인은 business-mcp(3)의 응답 길이 제한(3800자): 길면 `eta`·이름을 먼저 빼고, 더 길면 뒤쪽 정류장을 자른다. 그래프는 시각을 만들지 않고, 잘렸을 때는 "카드 경로는 정류장 n/m곳만" 사유를 붙인다. 카드에 시각을 실으려면 3과 정해야 한다 |
| `check_03a.py`가 가짜 근무조를 고정 (#91) | 실제 데이터로 돌리려면 코드를 고쳐야 함 | 근무조 ID를 두 번째 인자로 받는다. 빼면 가짜 MCP 근무조 |

---

## 5. 헷갈리기 쉬운 것

- **`seq`(이벤트 순번)는 backend가 매긴다.** agent는 보내지 않는다 (asyncapi 0.3).
- **`thread_id`는 작업 단위 `{shiftId}:{taskId}`**이고 backend가 `threadId`로 보낸다. 근무조 단위가 아니다.
- **backend의 가짜 에이전트(`FAKE_AGENT_ENABLED`)는 진짜 agent를 띄울 때 꺼야 한다.** 둘 다 켜면 이벤트가 두 번 나간다.
- **agent를 다시 띄울 때는 이전 프로세스를 먼저 끈다.** 같은 Kafka 그룹에 남아 있으면 일을 나눠 가져가 옛 코드가 처리한다.
- **변경 실행 여부는 답변 문장이 아니라 `TOOL_END`의 `ok`로 본다.**
