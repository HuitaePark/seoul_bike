# MCP 명세 (business-mcp, rag-mcp)

| 항목 | 내용 |
| --- | --- |
| 문서 상태 | 초안 v0.5 (specs internal-api v0.5·openapi v0.4, PR #12 리뷰 반영) |
| 작성일 | 2026-10-20 |
| 담당 | 3 MCP · RAG |
| 사용 | 1 에이전트 그래프 (MCP Client), 2 Harness (도구 허용 목록, 승인, 마스킹), 4 백엔드 (`/internal/**`) |
| 계약 원본 | 도구와 내부 API 대응은 [internal-api.yaml](specs/internal-api.yaml), 근거 표시는 [openapi.yaml](specs/openapi.yaml)의 `Source`. **이 문서와 specs가 다르면 specs가 우선**이다 |
| 코드 | `business-mcp/`, `rag-mcp/` (FastMCP 4.1, 색인기 `rag-indexer` 포함), 근거 문서 `docs/evidence/`, 컨테이너 `infra/compose.yaml` |

---

## 1. 서버 구성

| 서버 | 주소 (docker network) | 전송 | 도구 접두어 | 하는 일 |
| --- | --- | --- | --- | --- |
| `business-mcp` | `http://business-mcp:9101/mcp` | Streamable HTTP | `business_` | 재배치 조회·계산·변경. 백엔드 `/internal/**`만 호출 (`X-Internal-Token`) |
| `rag-mcp` | `http://rag-mcp:9102/mcp` | Streamable HTTP | `rag_` | 근거 문서 검색, 원문 Resource. 업무 데이터는 바꾸지 않음 |

에이전트 연결은 [HARNESS.md](HARNESS.md) 3.2 (`MultiServerMCPClient`, 접두어 `business_`, `rag_`).

**금지 (PR마다 확인):** `business-mcp`의 DB 직접 접근·업무 규칙 복제(위험 판정, 변경 등급, 승인 확인은 백엔드), `rag-mcp`의 업무 데이터 변경.

---

## 2. 공통 규칙

### 2.1 이름과 값

| 대상 | 규칙 | 예 |
| --- | --- | --- |
| 도구 인자 | 내부 API 필드의 snake_case (HARNESS 3.6 인자 검증과 같음) | `station_id`, `exclude_station_ids` |
| 도구 결과 필드 | **specs 그대로 camelCase**, enum은 대문자 | `truckId`, `forecastBikes`, `candidateId`, `PICKUP` |
| 권역 ID | 영문·숫자·하이픈 (대문자 허용) | `S05`, `gwangjin-seongdong` |
| 근무조 ID | `{regionId}:{yyyy-MM-dd}:{AM\|PM\|EVE}` | `S05:2026-10-21:AM` |
| 대여소 ID | `ST-숫자` | `ST-1023` |
| 시각 | ISO 8601, KST 오프셋 필수 | `2026-10-21T07:00:00+09:00` |
| 자전거 수 | 정수, 단위 대 | |

결과를 camelCase 그대로 두는 이유: Harness의 기사 범위 제한(다른 트럭의 `truckId` 항목 제거, NFR-06)과 인자 검증(`candidateId`, `stationId`, `docks`, `actualBikes`)이 계약 필드 이름을 읽는다.
권역 ID는 6번 데이터 담당이 `N01`~`S09` 형식을 제안했다 (합의 전). MCP는 두 형식을 모두 받는다.

### 2.2 결과 공통

- 모든 결과에 `source`(호출한 내부 API, 예 `GET /internal/stations/risk`)를 넣는다. 근거 패널의 `Source{kind: TOOL}`에 쓴다.
- specs가 배열로 주는 응답은 `{source, <목록 이름>: [...], truncated, total}`로 감싼다.
- **결과 길이는 3,800자 안**(`DEFAULT_MAX_CHARS`). Harness는 `tools.result_max_chars`(4,000자)에서 글자 단위로 자르는데, JSON 중간이 잘리면 Harness가 결과를 파싱하지 못해 `candidateId`·`planId` 검증이 거부된다. 그래서 MCP가 먼저 온전한 JSON으로 줄인다.
  - 목록: `limit`(기본 20, 최대 50)으로 자르고, 그래도 길면 뒤쪽부터 잘라 `truncated: true`, `total`.
  - 계획·후보: `diffs`의 before/after를 정류장 ID 목록(+`removed`, `added`)으로 줄인다. 그래도 길면 정류장의 `stationName`, `eta`, 끝난 정류장, `reason` 순으로 빼고(`plan.omitted`), 마지막으로 가장 긴 경로의 뒤쪽 정류장을 자른다(`plan.truncated`, `plan.totalStops`). 가짜 MAJOR 후보: 4,301자 -> 2,177자.
- 감사 기록·현장 보고는 backend가 시간 순서대로 주므로 MCP가 뒤집어 최신순으로 준다. `since`는 그 시각을 뺀다 (계약 0.5.0).
- 기사 이름은 백엔드가 마스킹해서 준다 (`driver: "기사 A"`). Harness도 한 번 더 마스킹한다.

### 2.3 오류 형식 (`ToolError`)

백엔드는 RFC 9457 `ToolProblem`(`code`, `detail`, `suggestions`)을 준다. MCP는 이를 모델용 JSON으로 바꾼다.

```json
{"code": "STATION_NOT_FOUND", "message": "'ST-9999' 대여소가 없습니다. 권역 안의 비슷한 대여소: ST-1023, ST-1031",
 "retryable": true, "hint": {"candidates": ["ST-1023", "ST-1031"]}}
```

| 백엔드 응답 | MCP `code` | `retryable` | `hint` |
| --- | --- | --- | --- |
| 404, 409, 400 `ToolProblem` | 백엔드 `code` 그대로 (목록은 `ToolProblem.code`) | `suggestions`가 있거나 400·422일 때만 true | `candidates` = `suggestions` |
| 본문 없는 401·403·404·409 | `UNAUTHORIZED`·`FORBIDDEN`·`NOT_FOUND`·`CONFLICT` | false | |
| 403 `APPROVAL_REQUIRED` | `APPROVAL_REQUIRED` | false (사람 승인을 기다려야 함) | |
| 5xx, 타임아웃 | `UPSTREAM_UNAVAILABLE` | true | `retry_after_s` |
| MCP 인자 검사 실패 | `INVALID_ARGUMENT` (시각 형식, 기간 순서, 필수 `value`) | true | 예시 |
| 승인 인자(`approval_id`, `task_id`) 없음 | `APPROVAL_REQUIRED` | false | |
| 스키마 위반 (범위, enum, 패턴) | FastMCP가 Pydantic 메시지로 돌려줌 | | 메시지에 허용 범위 포함 |

### 2.4 승인 전달

변경 도구 3개는 `approval_id`, `task_id` 인자를 갖지만 **모델은 보지도 넣지도 못한다.** Harness가 모델에게 주는 스키마에서
이 두 인자를 지우고(`hide_injected_args`), 승인된 호출을 실행할 때 채운다 (harness.yaml `guardrails.injected_args`).

1. 모델이 변경 도구를 고르면 Harness가 실행 전에 멈추고 승인 카드(`APPROVAL_REQUIRED`)를 보낸다.
2. 카드 수와 승인자
   - `submit_plan`, `add_constraint`: 관리자 카드 한 장.
   - `propose_route_change`: `request_route_plan` 결과의 `requiredApprovers` 순서. MINOR는 `[DRIVER]` 한 장, MAJOR는 `[MANAGER, DRIVER]` 두 장. **기사 카드 승인이 곧 기사 수락**이라 따로 수락 단계가 없다 (specs openapi v0.3, HARNESS 10절 (나)). 앞 카드가 거절되면 뒤 카드는 보내지 않고 기존 경로를 유지한다.
3. 사람이 승인하면 backend가 기록하고 `ai.tasks` `RESUME`(approvalId)을 보낸다.
4. Harness가 마지막 카드의 approvalId와 taskId를 채워 도구를 실행한다. business-mcp는 값을 요청 본문의 `approvalId`, `taskId`로 옮긴다. 값이 없으면 `APPROVAL_REQUIRED`.
   승인 규칙이 없는 `request_route_plan`은 Harness가 `task_id`를 채우지 않는다. business-mcp는 `X-Task-Id` 헤더가 오면 그 값으로 `requestedBy.taskId`를 채운다 (Harness 헤더 주입기에 `X-Task-Id` 추가를 2번에게 제안).
5. backend가 필요한 승인이 순서대로 모두 `APPROVED`인지 한 번 더 확인한다 (아니면 403). 경로 변경의 기사 승인은 그 트럭 기사의 것이어야 한다.

grade가 `NONE`이면 바꿀 것이 없으므로 `propose_route_change`를 부르지 않는다.

---

## 3. `business-mcp` 도구 11개

| # | 도구 | 구분 | 내부 API | Harness 허용 작업 |
| --- | --- | --- | --- | --- |
| 1 | `get_risk_stations` | 조회 | `GET /internal/stations/risk` | 전체 |
| 2 | `get_station_detail` | 조회 | `GET /internal/stations/{stationId}` | 전체 |
| 3 | `get_forecast` | 조회 | `GET /internal/forecasts` | 전체 |
| 4 | `get_current_plan` | 조회 | `GET /internal/shifts/{shiftId}/plan` | 전체 |
| 5 | `get_truck_status` | 조회 | `GET /internal/shifts/{shiftId}/trucks` | 전체 |
| 6 | `get_field_reports` | 조회 | `GET /internal/shifts/{shiftId}/reports` | 전체 |
| 7 | `get_audit` | 조회 | `GET /internal/shifts/{shiftId}/audit` (계약 0.5.0, PR #20) | qa (2번 결정, PR #27) |
| 8 | `request_route_plan` | 계산 | `POST /internal/plans/solve` | plan_draft, field_report, manager_request |
| 9 | `add_constraint` | 변경 | `POST /internal/constraints` | manager_request |
| 10 | `submit_plan` | 변경 | `POST /internal/plans/{planId}/activate` | plan_draft |
| 11 | `propose_route_change` | 변경 | `POST /internal/routes/{truckId}/apply` | field_report |

힌트: 조회 `readOnlyHint=true`, 계산 `destructiveHint=false`, 변경 `destructiveHint=true`. Harness는 `destructiveHint`로 변경 도구를 구분한다.

| 도구 | 인자 | 결과 |
| --- | --- | --- |
| `get_risk_stations` | `region_id`, `horizon_h`=3 (1~5), `risk`=ALL (EMPTY, OVERFLOW, ALL), `limit`=20 | `stations[]`: stationId, name, bikes, forecastBikes, docks, risk, role, neededBikes(양수 회수, 음수 배치), priority, emptyAt, reasons |
| `get_station_detail` | `station_id` | 대여소 상태 + district, recentReports |
| `get_forecast` | `station_ids` (20곳 이하), `start`, `horizon_h`=3 | `forecasts[]`: stationId, start, horizonH, rentals, returns, modelVersion |
| `get_current_plan` | `shift_id` | planId, version, status, provisional, routes[stops], unserved, explanation |
| `get_truck_status` | `shift_id`, `truck_id`(선택) | `trucks[]`: truckId, capacity, load, lat, lon, shiftEnd, remainingStops |
| `get_field_reports` | `shift_id`, `since`(선택), `limit`=20 | `reports[]` (최신순): reportId, truckId, driver, stationId, actualBikes, issue, memo, reportedAt |
| `get_audit` | `shift_id`, `since`(선택), `limit`=20 | `events[]` (최신순): auditId, occurredAt, type, actor, targetId, detail |
| `request_route_plan` | `shift_id`, `kind`=PLAN (PLAN, REPLAN), `truck_ids`(선택), `exclude_station_ids`(선택) | candidateId, status(OK, TIMEOUT_PROVISIONAL), plan, grade(NONE, MINOR, MAJOR), requiredApprovers, diffs |
| `add_constraint` | `shift_id`, `type`, `station_ids`, `valid_from`, `valid_to`, `reason`, `value`(선택) | constraintId와 저장된 제약 |
| `submit_plan` | `plan_id` | 활성화된 계획 |
| `propose_route_change` | `truck_id`, `candidate_id` | 적용된 트럭 경로 |

변경 도구와 `request_route_plan`에는 위 인자 말고 Harness가 채우는 `approval_id`, `task_id`가 더 있다 (2.4).

- `horizon_h` 최대 5: 근거 문서 [ref-forecast-kim-2023](evidence/ref-forecast-kim-2023.md) 7절 (논문 본문 3~5시간), 6번 합의 29번 제안(최대 5시간)과 같다. specs는 6까지 받으므로 MCP가 더 좁게 받는다.
- `add_constraint.type`: `TARGET_ADJUST`, `STATION_SUSPENDED`(대여·반납 중지), `STATION_BLOCKED`(트럭 진입 불가), `INVENTORY_OVERRIDE`, `PRIORITY_BOOST`. `TARGET_ADJUST`, `INVENTORY_OVERRIDE`는 `value` 필수.
- `request_route_plan`: 현장 실제 재고는 기사 보고로 백엔드에 이미 들어가 있으므로 따로 넘기지 않는다. 이번 계산에서 뺄 대여소만 `exclude_station_ids`로 넘긴다.
- `propose_route_change`: 경로를 직접 받지 않고 `request_route_plan(kind=REPLAN)`이 만든 `candidateId`를 적용한다. 승인 카드는 후보의 `requiredApprovers` 순서를 따른다 (2.4).

---

## 4. `rag-mcp`와 `rag-indexer`

```
docs/evidence/*.md --(rag-indexer)--> Chunking --> Embedding --> Indexing --> vectordb (pgvector)
                                                                                  |
agent --rag_search_docs--> rag-mcp: 질문 임베딩 -> 코사인 후보 -> 키워드 점수 섞기 -> chunk + 출처
```

### 4.1 도구 `search_docs(query, top_k=5, doc_types=None)`

| 인자 | 설명 |
| --- | --- |
| `query` | 자연어 질문 (1~500자) |
| `top_k` | 1~10 (Harness가 5로 제한) |
| `doc_types` | `ops_standard`(운영 기준), `seoul_notice`(서울시 공지), `reverse_analysis`(역산 분석), `handover`(인수인계 양식), `reference_paper`(외부 논문), `general`(종류 미지정) |

결과: `{as_of, source, embedding{model, dim}, chunks[], truncated}`. `as_of`는 색인을 만든 시각이다.

그래프 `doc_sources`(PR #11, 머지됨)가 이 모양의 `chunks[]`를 계약 `Source{kind: DOC}`로 옮긴다 (제목 `title · section`, uri는 chunk `uri`, excerpt는 `text` 앞 120자). Harness `contract.sources`도 같은 모양을 읽도록 2번에게 제안했다 (ISSUES ISS-23).

| chunk 필드 | 뜻 | `Source` 대응 |
| --- | --- | --- |
| `title` | 문서 제목 | `title` |
| `uri` | `doc://{doc_id}#{절 번호}` | `uri` |
| `text` | 본문 (절의 `출처:` 줄 포함) | `excerpt` |
| `citation` | 사람이 읽는 출처. `[보조]` 조각은 그 조각에 이름이 나오는 보조 출처만 | 계약에 없음 (PR #5 제안) |
| `basis` | `primary`(팀 저장소) / `supplementary`(저장소 밖 자료) | 계약에 없음 (PR #5 제안) |
| `status` | 문서의 한계 (추정치, 가상 값 등). 답변에도 함께 밝힌다 | |
| `section`, `doc_id`, `doc_type`, `version`, `data_period`, `score` | 절 이름, 색인 정보, 관련도(0~1) | |

### 4.2 검색 방식

| 단계 | 내용 | 코드 |
| --- | --- | --- |
| 1 | 질문을 색인과 **같은 모델**로 임베딩. rag-mcp는 시작할 때 `rag_index`의 모델 이름·차원이 다르면 뜨지 않는다 | `pgvector_store.py` |
| 2 | `rag_chunk`에서 코사인 거리(`<=>`, HNSW)로 후보 `max(top_k x 4, 20)`개 | |
| 3 | 혼합 점수 = 0.7 x 코사인 + 0.3 x 키워드(BM25, 한국어 글자 2-gram, 후보 중 최댓값으로 나눔) | `store.py` |
| 4 | 가장 높은 점수를 1로 맞추고, 같으면 `primary`를 앞에 | |

DB 없이 돌릴 때(`RAG_STORE=memory`)는 파일을 읽어 키워드 점수만 쓴다. 테스트와 로컬 확인용이다.

### 4.3 임베딩 모델

| 항목 | 값 |
| --- | --- |
| 기본 모델 | `intfloat/multilingual-e5-large` (1024차원), fastembed(ONNX, PyTorch 없음). 질문 앞 `query: `, 문서 앞 `passage: ` |
| 고른 이유 | 10문항 점검에서 다국어 MiniLM(384)·mpnet(768)보다 벡터 검색 적중이 높음 ([eval/README.md](../rag-mcp/eval/README.md) 1절) |
| 자원 | 메모리 약 1.6GB(프로세스), rag-mcp 컨테이너 약 2.0GB. 질문 하나 약 36ms. 이미지에 모델 포함(약 4.2GB) |
| 바꾸기 | `EMBED_MODEL` 빌드 인자·환경 변수. rag-indexer가 새 모델로 다시 색인한다 (차원이 바뀌면 표를 새로 만든다) |
| 테스트 | `EMBED_PROVIDER=hash` (네트워크 없는 글자 2-gram 해시, 256차원) |

검색 품질 (2026-10-20, 조정에 쓰지 않은 확인용 8문항): 혼합 1위 5/8, 3위 안 7/8, 키워드만도 같은 점수. 작은 문서 묶음에서는 혼합의 이점이 아직 드러나지 않는다 ([eval/README.md](../rag-mcp/eval/README.md) 3절).

### 4.4 색인기 `rag-indexer`

`docker compose run --rm rag-indexer`, 또는 `rag-indexer [--docs DIR] [--force]`. compose에서는 rag-mcp보다 먼저 한 번 돌고 끝난다.

| 표 (vectordb) | 내용 |
| --- | --- |
| `rag_doc` | 문서 원문·머리말 (Resource `doc://{doc_id}`가 읽음) |
| `rag_chunk` | chunk 본문, 출처(`citation`, `basis`, `section`, `doc_version`, `status`), `embedding vector(dim)`, HNSW 코사인 색인 |
| `rag_index` | 색인 기록: 모델 이름, 차원, 문서 묶음 해시, 문서·chunk 수, 만든 시각 |

문서 묶음 해시와 모델이 직전 기록과 같으면 다시 만들지 않는다 (`{"skipped": true}`).

### 4.5 Resource

| URI | 내용 |
| --- | --- |
| `doc://{doc_id}` | 근거 문서 원문 (Markdown, 머리말 포함). `#절 번호`가 붙어도 같은 원문 |
| `doc://index` | 문서 목록 (doc_id, title, doc_type, version, updated, uri) |

여러 서버를 한 Client로 묶으면 Resource URI에 서버 이름이 붙을 수 있다 (`doc://rag/{doc_id}`). 근거 패널에는 `doc://{doc_id}`를 쓴다.

### 4.6 근거 문서 (`docs/evidence/`)

위치와 머리말은 6번 합의 21번 제안(`title`, `doc_id`, `version`, `updated`, `source`, `status`)을 따른다. 작성 규칙은 [docs/evidence/README.md](evidence/README.md).

| 문서 | doc_id | doc_type | 작성 |
| --- | --- | --- | --- |
| 재배치 운영 기준 | [ops-rebalancing-standard](evidence/ops-rebalancing-standard.md) | `ops_standard` | 3 |
| 서울시 공지 (2025 불꽃축제) | [notice-seoul-fireworks-2025](evidence/notice-seoul-fireworks-2025.md) | `seoul_notice` | 3 |
| 근무조 인수인계 요약 양식 | [handover-shift-summary-template](evidence/handover-shift-summary-template.md) | `handover` | 3 |
| 수요예측 논문 요약 (보조) | [ref-forecast-kim-2023](evidence/ref-forecast-kim-2023.md) | `reference_paper` | 3 |
| 따릉이 트럭 재배치 역산 요약 | `rebal-summary` (PR #10) | `reverse_analysis` (머리말에 없으면 MCP가 채움) | 6 |

**Chunking:** `##` 절 단위, 절의 `출처:` 줄을 함께 넣는다. 1,200자를 넘는 절의 표는 행 단위로 나누고 표 머리 줄을 붙인다. `[보조]` 문단은 따로 떼어 `basis: supplementary`. `reference_paper` 문서와 제목에 `[보조]`가 있는 절은 전부 `supplementary`. 임베딩에는 `제목 + 절 이름 + 본문`을 넣는다.

---

## 5. 실행과 테스트

| 방법 | 명령 |
| --- | --- |
| 전체 (서버와 같은 방식) | `docker compose -f infra/compose.yaml --env-file <.env> up -d --wait vectordb rag-indexer rag-mcp business-mcp` |
| business-mcp 테스트 | `cd business-mcp && pip install -e ".[test]" && python -m pytest -q` |
| rag-mcp 테스트 (DB 없이) | `cd rag-mcp && pip install -e ".[test]" && python -m pytest -q` |
| rag-mcp 테스트 (pgvector·실제 모델까지) | `RAG_TEST_DSN=postgresql://...  RAG_TEST_FASTEMBED=1 python -m pytest -q` |
| 검색 품질 점검 | `VECTOR_DSN=... python eval/retrieval_eval.py` |

| 환경 변수 | 서버 | 뜻 |
| --- | --- | --- |
| `INTERNAL_MODE` (.env `MCP_INTERNAL_MODE`) | business-mcp | `fake`(기본, 가짜 `/internal`), `http`, `hybrid`(backend가 연 API만 http) |
| `INTERNAL_HTTP_METHODS` (.env `MCP_INTERNAL_HTTP_METHODS`) | business-mcp | hybrid에서 http로 부를 것. 기본 `field_reports,add_constraint,audit_events` (PR #19·#20) |
| `BACKEND_URL`, `INTERNAL_TOKEN` | business-mcp | `http` 모드의 백엔드 주소와 서비스 토큰 |
| `RAG_STORE` | rag-mcp | `pgvector`(PG 접속 정보가 있으면 기본) 또는 `memory` |
| `PGHOST`·`PGUSER`·`PGPASSWORD`·`PGDATABASE` 또는 `VECTOR_DSN` | rag-mcp, rag-indexer | vectordb 접속 |
| `EMBED_PROVIDER`, `EMBED_MODEL` | rag-mcp, rag-indexer | 임베딩 공급자·모델. 두 서비스가 같아야 한다 |
| `RAG_DOCS_DIR` | rag-indexer, memory 저장소 | 근거 문서 폴더 (기본 `docs/evidence/`) |
| `MCP_HOST`, `MCP_PORT` | 둘 다 | 기본 `0.0.0.0`, 9101 / 9102 |

테스트는 인메모리 Client(`Client(server)`)로 MCP 프로토콜을 그대로 거친다. 가짜 `/internal`의 대여소·재고는 가짜 값이다.

---

## 6. 열린 항목

| 항목 | 상대 | 내용 |
| --- | --- | --- |
| 가짜 `/internal` 기본값 | 4 백엔드 | `/internal`이 다 열리면 기본값을 `http`로 바꾸고 `fake_internal.py`를 `tests/`로 옮김 (PR #12 리뷰). 그전에는 `hybrid`로 열린 API만 실제로 부를 수 있음 |
| `Source`의 `citation`·`basis` | 4 백엔드, 화면 담당 | 근거 패널에 출처·등급을 보이려면 openapi `Source`에 필요 (PR #5 제안, 닫힘) |
| 대여소 ID 형식 | 전원 | 6번 합의 1번 제안 `"108"`. MCP는 `ST-1023`과 `108` 둘 다 받는다. Harness `id_pattern`, 승인 카드 화면도 함께 바꿔야 함 |
| 권역 ID 형식 | 6 데이터, 4 백엔드 | `N01`~`S09` 제안 (합의 2번). MCP는 대문자도 받음 |
| `Forecast` 시간별 값 | 6 데이터, 4 백엔드 | `rentalsH`·`returnsH` 제안 (합의 15번). 백엔드가 주면 MCP는 그대로 전달 |
| `horizonH` 최대값 | 4 백엔드 | specs 6, MCP 5. 6번도 5시간 제안 (합의 29번) |
| CI `mcp` 잡 | 4·5 인프라 | 배포 `paths`는 PR #14로 반영됨. 테스트 잡(`mcp`, pgvector 서비스)은 `.github/workflows` 권한이 필요해 PR #12 설명 7절 그대로 추가 부탁 |
| 근거 패널 화면 | 3 (M4) | `frontend/src/features/evidence`. 계약의 `Source` 필드가 정해진 뒤 |
| 불꽃축제 폐쇄 대여소 목록 | 6 데이터 | 서울시 첨부 PDF를 대여소 ID와 맞춰 [notice-seoul-fireworks-2025](evidence/notice-seoul-fireworks-2025.md) 5절 예시를 채움 |
| 검색 개선 | 3 | 확인용 질문에서 놓친 "재고 불일치 -> INVENTORY_OVERRIDE" 문항. 문서가 늘면 혼합 비중 다시 점검 |
