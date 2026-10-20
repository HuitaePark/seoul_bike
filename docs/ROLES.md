# 역할 분담 (6인)

| 항목 | 내용 |
| --- | --- |
| 문서 상태 | 초안 v0.1 |
| 작성일 | 2026-10-20 |
| 기준 | [전체 구성도](images/architecture.png), [뷰 7종](images/views/), AI-Agent Capstone(+MCP) 평가표 |

> 사람 이름은 팀에서 채운다. 추천 배치: **4 최적화·예측은 민규 님**(기존 `or-tools/rebalancing` 작성), **5 시뮬레이터·평가는 역산 분석 작성자**.

---

## 1. 한눈에 보기

| # | 역할 | 맡는 컨테이너·모듈 | 평가표 항목 (배점) | 담당자 |
| --- | --- | --- | --- | --- |
| 1 | 에이전트 | `agent` (FastAPI · LangGraph · Harness) | AI Agent 설계 15, 구현 15, Harness 20 | |
| 2 | MCP · RAG | `business-mcp`, `rag-mcp`, `vectordb`, `rag-indexer` | MCP 설계·구현 20 | |
| 3 | 백엔드 | `backend` (Spring Boot) | 인증 10, 대기열 가산점 10 | |
| 4 | 최적화 · 예측 · 인프라 | `optimizer`, `osrm`, `docker-compose`, CI | 아키텍처 설계 10 (공동) | |
| 5 | 시뮬레이터 · 평가 · 기획 | `sim`, 평가셋, PRD, ISO/IEC 25010 | Biz. 아이템 10, 아키텍처 설계 10 (공동) | |
| 6 | 프론트엔드 | `frontend` (Vue 3) | AI Agent 구현 15 (진행 이벤트·승인 화면, 공동) | |

---

## 2. 역할별 작업

### 1. 에이전트

**맡는 것:** LangGraph 근무조 그래프, Harness 8요소, 프롬프트, 진행 이벤트 발행.

- [ ] `ShiftState`와 그래프 골격 (LLM 없이 `ingest → optimize → manager_review(interrupt) → activate`)
- [ ] MCP Client로 `business-mcp`, `rag-mcp` 연결 (`mcpServers` 설정, 도구 접두어)
- [ ] 모델이 도구를 고르는 노드: 계획 초안, 현장 보고 해석·재경로, 상황 반영, 질의
- [ ] 변경 도구(`destructiveHint`) 앞 `interrupt`, checkpointer 저장, 같은 `thread_id`로 재개
- [ ] Harness: Instructions(`prompts/*.md`), Tools(결과 길이 제한·ToolMessage), Context(토큰 예산·요약), State·Memory, Agent Loop(8턴·120초·같은 도구 3회), Guardrails(승인·마스킹), Recovery(429·503 2회 재시도·대체 모델·FAILED), Observability(LangSmith, `trace_id`)
- [ ] `ai.tasks` 소비, `ai.events` 발행 (`TOKEN`, `TOOL_START`, `TOOL_END`, `APPROVAL_REQUIRED`, `DONE`, `FAILED`)

**완료 기준:** 03a·03b 시퀀스가 실제로 돌고, LangSmith에서 한 작업의 Node·도구·토큰·지연이 `trace_id` 하나로 보인다.

**금지:** 업무 DB 직접 쓰기 (checkpointer만 허용), 숫자를 모델이 직접 만들기.

### 2. MCP · RAG

**맡는 것:** 재배치 도구 서버와 문서 검색 서버, 근거 문서.

- [ ] `business-mcp` 도구 10개 ([05 연결 뷰](images/views/05-mcp-connection.png) 기준): 조회 6, 계산 1(`request_route_plan`), 변경 3(`add_constraint`, `submit_plan`, `propose_route_change`)
- [ ] docstring에 받는 값·돌려주는 값·단위 명시, 변경 도구에 `destructiveHint`
- [ ] `ToolError`에 재시도 정보 포함 (예: 권역 내 가까운 대여소 목록)
- [ ] `rag-mcp`: `search_docs(query, top_k)`가 chunk와 출처를 반환, Resource `doc://{doc_id}`로 원문 제공 (**1개 이상 필수**)
- [ ] `rag-indexer`: Chunking → Embedding(모델 이름·차원 기록) → Indexing(출처·버전)
- [ ] 근거 문서 작성: 재배치 운영 기준, 서울시 공지(행사·대여 중지), 역산 분석 보고서 요약, 인수인계 요약 양식
- [ ] 인메모리 Client로 도구 단위 테스트

**완료 기준:** agent가 Streamable HTTP(`/mcp`)로 두 서버의 도구를 찾고 호출하며, 없는 대여소를 넣으면 모델이 오류 메시지를 보고 다시 호출한다.

**금지:** `business-mcp`의 DB 직접 접근·업무 규칙 복제 (`/internal/**`만 호출), `rag-mcp`의 업무 데이터 변경.

### 3. 백엔드 (Spring Boot)

**맡는 것:** 업무 규칙과 데이터 변경의 유일한 창구, 인증, 작업 접수, SSE 중계, 대기열.

- [ ] 업무 API `/api/**`: 대여소 현황, 계획, 변경안, 현장 보고, 성과 조회
- [ ] 작업 API: `POST /api/ai/tasks` → 202 + `taskId`, `GET .../{id}`, `GET .../{id}/stream`(SSE), `POST .../{id}/approval`, `GET /api/ai/tasks?mine=true`
- [ ] 내부 API `/internal/**` (docker network 안에서만): business-mcp가 호출
- [ ] **인증**: Spring Security + JWT, 역할 `MANAGER` · `DRIVER` · `EXECUTIVE`, 기사는 자기 트럭만
- [ ] 변경 등급 규칙(minor·major), 승인 기록, 감사 로그
- [ ] Kafka: `ai.tasks` · `solve.requests` 발행, `ai.events` · `solve.results` 소비, **실패 시 `*.retry` → `*.dlq`** (가산점)
- [ ] 따릉이 실시간·기상청 수집 스케줄러 (5~10분)
- [ ] PostgreSQL 스키마 (Flyway)

**완료 기준:** 로그인한 관리자가 작업을 만들면 202를 받고, SSE로 진행 이벤트가 오며, 처리에 실패한 작업이 재시도 후 DLQ에 쌓인다.

**금지:** LLM 호출.

### 4. 최적화 · 예측 · 인프라

**맡는 것:** OR-Tools 경로 계산, 수요 예측, 컨테이너 실행 환경.

- [ ] 기존 `or-tools/rebalancing`을 PRD v0.2에 맞추기: 트럭 용량 15대, 거치대 상한은 설정값, 빈 대여소 우선 벌점, 시간 차원(근무조 종료), 탐색 한도는 해 개수로 고정
- [ ] 재경로: 현재 위치·적재량에서 남은 정류장만 다시 풀기
- [ ] `solve.requests` 소비 → `solve.results` 발행 (요청 ID로 중복 결과 방지)
- [ ] 예측: 과거 평균 베이스라인 → LightGBM, `predict(station, start, horizon_h)`
- [ ] OSRM (`osrm-init` 전처리), 이동 시간 행렬 캐시
- [ ] `docker-compose.yml`, Redpanda `topics-init`, `.env` 템플릿, GitHub Actions CI

**완료 기준:** `docker compose up` 한 번으로 전체가 뜨고, 광진·성동 권역 200곳·트럭 5대 재경로가 10초 안에 나온다.

**금지:** LLM 호출, DB 직접 접근.

### 5. 시뮬레이터 · 평가 · 기획

**맡는 것:** 성과 증명(UR-16), Harness 평가셋, 문서와 품질 근거, 인터뷰.

- [ ] 대여이력 재생 시뮬레이터, 정책 4종: 재배치 없음, 단순 규칙, **현행 운영 재현(역산 기록)**, 본 시스템
- [ ] 지표: 빈 대여소-분(주 지표), 과적재 대여소-분, 트럭-시간당 해소 대수
- [ ] 시나리오 평가셋: 불꽃축제 대여 중지, 진입 불가, 재고 불일치, 트럭 부족. 프롬프트·모델을 바꿀 때마다 전후 결과표
- [ ] 실시간 API로 역산 재고(배치 직전 2대, 회수 직전 31대) 검증
- [ ] ISO/IEC 25010 품질 특성 매핑 (성능 효율성, 신뢰성, 보안, 사용성, 유지보수성 등)
- [ ] PRD·ARCHITECTURE 문서 갱신, 현장 인터뷰, 발표 시나리오

**완료 기준:** 같은 날짜·같은 트럭 수에서 네 정책의 지표표가 나오고, Harness 변경 전후 평가표가 남는다.

### 6. 프론트엔드 (Vue 3)

**맡는 것:** 관제·기사·책임자 화면, 진행 이벤트 표시, 승인 화면.

- [ ] 로그인, 역할별 라우팅 (`/control`, `/driver`, `/report`)
- [ ] 관제: 권역 현황 지도, 계획 검토(포기 거점·근거), **승인 카드**, 실행 모니터링, 변경안 상세, 질의 패널
- [ ] 기사 PWA: 작업 목록, 정류장 보고(3번 터치), 변경 제안(정차 중에만), 전송 실패 시 다시 보내기
- [ ] 성과 리포트: 정책 4종 지표 비교
- [ ] SSE 이벤트 표시: `TOKEN`(답변 영역), `TOOL_START`·`TOOL_END`(상태 표시), `APPROVAL_REQUIRED`(승인 카드), `DONE`·`FAILED`(목록 갱신)
- [ ] 화면은 다섯 작업 API와 업무 API만 호출 (AI 서버 주소 노출 금지)

**완료 기준:** 데모 순서(현황 → 계획 승인 → 기사 보고 → 변경 수락 → 모니터링 → 성과)가 화면만으로 이어진다.

**금지:** 권한 판단, AI 서버 직접 호출.

---

## 3. 1주차에 함께 확정할 계약

계약을 확정한 날 각자 가짜 구현을 올려서 서로 기다리지 않는다.

| 계약 | 주관 | 사용 | 산출물 |
| --- | --- | --- | --- |
| 작업 API · 업무 API | 3 백엔드 | 6, 5 | OpenAPI 명세 |
| 진행 이벤트 형식 (`TOKEN` 등) | 1 에이전트 | 3, 6 | 이벤트 JSON 스키마 |
| MCP 도구 이름·인자·반환 | 2 MCP · RAG | 1 | 도구 명세 표 |
| `/internal/**` | 3 백엔드 | 2 | OpenAPI 명세 (내부) |
| Kafka 토픽·메시지 | 3 백엔드 | 1, 4 | 토픽 표, 메시지 스키마, `event_id` |
| `solve` 요청·결과, 예측 시그니처 | 4 최적화 | 3, 5 | 스키마, `DemandPredictor` |

---

## 4. 단계

| 단계 | 목표 | 주로 움직이는 역할 |
| --- | --- | --- |
| M1 | 계약 확정, 가짜 구현, `docker compose up`, 실시간 수집 시작 | 전원 |
| M2 | 인증, 작업 API 202·SSE, 최적화 PRD v0.2 반영, MCP 조회 도구 | 3, 4, 2, 6 |
| M3 | 그래프 + 승인 `interrupt`·재개, RAG, 계획 승인 화면 (03a 통과) | 1, 2, 6 |
| M4 | 현장 보고 → 재경로 → 기사 수락 (03b 통과), 재시도·DLQ | 1, 3, 6 |
| M5 | Harness 8요소 완성, 시뮬레이터 정책 4종, 평가표 | 1, 5 |
| M6 | ISO/IEC 25010 매핑, 발표 시나리오, 통합 데모 | 5, 전원 |

---

## 5. 운영 규칙

- **CODEOWNERS:** 모듈마다 담당자 한 명이 PR 승인권을 가진다. 계약 파일(OpenAPI, 이벤트·토픽·도구 스키마)은 두 명 이상 승인.
- **주간 통합 데모:** 매주 `docker compose up` 한 번으로 전체가 뜨는지 확인한다.
- **금지 사항 점검:** 평가에서 구성 요소별 금지 사항 위반을 본다. PR마다 해당 모듈의 금지 사항을 체크한다.
- **부하 조정:** 1 에이전트, 3 백엔드, 6 프론트엔드가 가장 무겁다. M3 이후 4는 3의 Kafka 재시도를, 5는 1의 평가셋을 돕는다.
