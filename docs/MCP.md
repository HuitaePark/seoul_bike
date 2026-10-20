# MCP 명세 (business-mcp, rag-mcp)

| 항목 | 내용 |
| --- | --- |
| 문서 상태 | 초안 v0.3 (specs internal-api v0.2·openapi v0.3, HARNESS v0.5에 맞춤) |
| 작성일 | 2026-10-20 |
| 담당 | 3 MCP · RAG |
| 사용 | 1 에이전트 그래프 (MCP Client), 2 Harness (도구 허용 목록, 승인, 마스킹), 4 백엔드 (`/internal/**`) |
| 계약 원본 | 도구와 내부 API 대응은 [internal-api.yaml](specs/internal-api.yaml), 근거 표시는 [openapi.yaml](specs/openapi.yaml)의 `Source`. **이 문서와 specs가 다르면 specs가 우선**이다 |
| 코드 | `business-mcp/`, `rag-mcp/` (FastMCP 4.1), 근거 문서 `docs/rag/` |

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
- 목록은 `limit`(기본 20, 최대 50)으로 자르고, 결과 JSON이 4,000자를 넘으면 목록 뒤쪽부터 잘라 `truncated: true`. 계획은 정류장이 50개를 넘으면 트럭별 앞 10개만 준다. Harness `tools.result_max_chars`(4000)와 같은 값이다.
- 기사 이름은 백엔드가 마스킹해서 준다 (`driver: "기사 A"`). Harness도 한 번 더 마스킹한다.

### 2.3 오류 형식 (`ToolError`)

백엔드는 RFC 9457 `ToolProblem`(`code`, `detail`, `suggestions`)을 준다. MCP는 이를 모델용 JSON으로 바꾼다.

```json
{"code": "STATION_NOT_FOUND", "message": "'ST-9999' 대여소가 없습니다. 권역 안의 비슷한 대여소: ST-1023, ST-1031",
 "retryable": true, "hint": {"candidates": ["ST-1023", "ST-1031"]}}
```

| 백엔드 응답 | MCP `code` | `retryable` | `hint` |
| --- | --- | --- | --- |
| 404, 409, 400 `ToolProblem` | 백엔드 `code` 그대로 (목록은 `ToolProblem.code`) | true | `candidates` = `suggestions` |
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
| 7 | `get_audit` | 조회 | `GET /internal/shifts/{shiftId}/audit` (**계약 미반영**, ISSUES ISS-06) | qa (harness.yaml에서 결정 전까지 빠짐) |
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

- `horizon_h` 최대 5: 근거 문서 [ref-forecast-kim-2023](rag/ref-forecast-kim-2023.md) 7절 (논문 본문 3~5시간). specs는 6까지 받으므로 MCP가 더 좁게 받는다.
- `add_constraint.type`: `TARGET_ADJUST`, `STATION_SUSPENDED`(대여·반납 중지), `STATION_BLOCKED`(트럭 진입 불가), `INVENTORY_OVERRIDE`, `PRIORITY_BOOST`. `TARGET_ADJUST`, `INVENTORY_OVERRIDE`는 `value` 필수.
- `request_route_plan`: 현장 실제 재고는 기사 보고로 백엔드에 이미 들어가 있으므로 따로 넘기지 않는다. 이번 계산에서 뺄 대여소만 `exclude_station_ids`로 넘긴다.
- `propose_route_change`: 경로를 직접 받지 않고 `request_route_plan(kind=REPLAN)`이 만든 `candidateId`를 적용한다. 승인 카드는 후보의 `requiredApprovers` 순서를 따른다 (2.4).

---

## 4. `rag-mcp`

### 4.1 도구 `search_docs(query, top_k=5, doc_types=None)`

| 인자 | 설명 |
| --- | --- |
| `query` | 자연어 질문 (1~500자) |
| `top_k` | 1~10 (Harness가 5로 제한) |
| `doc_types` | `ops_standard`(운영 기준), `seoul_notice`(서울시 공지), `reverse_analysis`(역산 분석), `handover`(인수인계 양식), `reference_paper`(외부 논문) |

결과: `{as_of, source, embedding{model, dim}, chunks[], truncated}`.

| chunk 필드 | 뜻 | `Source` 대응 |
| --- | --- | --- |
| `title` | 문서 제목 | `title` |
| `uri` | `doc://{doc_id}#{절 번호}` | `uri` |
| `text` | 본문 (절의 `출처:` 줄 포함) | `excerpt` |
| `citation` | 사람이 읽는 출처 | `citation` |
| `basis` | `primary`(팀 저장소) / `supplementary`(저장소 밖 자료) | `basis` (대문자로) |
| `section` | 절 이름 | `section` |
| `doc_id`, `doc_type`, `version`, `data_period`, `score` | 색인 정보, 관련도(0~1) | |

- 같은 점수면 `primary`를 앞에 둔다. 두 등급이 다른 값을 주면 `primary`가 우선이다.
- 결과가 없으면 빈 목록과 `message`(다시 검색하는 방법).
- M1은 메모리 안 BM25 키워드 검색(한국어 글자 2-gram)이다. 임베딩 모델이 정해지면 같은 `DocStore` 계약으로 pgvector 구현을 붙이고 `embedding.model`, `dim`을 기록한다.

### 4.2 Resource

| URI | 내용 |
| --- | --- |
| `doc://{doc_id}` | 근거 문서 원문 (Markdown, front matter 포함). `#절 번호`가 붙어도 같은 원문 |
| `doc://index` | 문서 목록 (doc_id, title, doc_type, version, verified_at, uri) |

여러 서버를 한 Client로 묶으면 Resource URI에 서버 이름이 붙을 수 있다 (`doc://rag/{doc_id}`). 근거 패널에는 `doc://{doc_id}`를 쓴다.

### 4.3 근거 문서 (`docs/rag/`)

**작성 원칙:** 팀 저장소 내용을 기본으로 쓰고, 저장소 밖 자료는 `[보조]`로 표시한다. 각 절 끝에 `출처:` 줄을 둔다.

| 문서 | doc_id | doc_type |
| --- | --- | --- |
| 재배치 운영 기준 | [ops-rebalancing-standard](rag/ops-rebalancing-standard.md) | `ops_standard` |
| 역산 분석 요약 | [analysis-reverse-estimation](rag/analysis-reverse-estimation.md) | `reverse_analysis` |
| 서울시 공지 (2025 불꽃축제) | [notice-seoul-fireworks-2025](rag/notice-seoul-fireworks-2025.md) | `seoul_notice` |
| 근무조 인수인계 요약 양식 | [handover-shift-summary-template](rag/handover-shift-summary-template.md) | `handover` |
| 수요예측 논문 요약 (보조) | [ref-forecast-kim-2023](rag/ref-forecast-kim-2023.md) | `reference_paper` |

front matter 필수: `doc_id`(파일 이름과 같게), `doc_type`, `title`, `version`, `verified_at`, `sources[]`(`role`: primary 또는 supplementary, `type`, `citation` 또는 `repo`·`commit`·`paths`). 선택: `data_period`.

**Chunking:** `##` 절 단위, 절의 `출처:` 줄을 함께 넣는다. 1,200자를 넘는 절의 표는 행 단위로 나누고 표 머리 줄을 붙인다. `[보조]` 문단은 따로 떼어 `basis: supplementary`. `reference_paper` 문서와 제목에 `[보조]`가 있는 절은 전부 `supplementary`.

---

## 5. 실행과 테스트

```bash
cd business-mcp && pip install -e ".[test]" && python -m pytest -q
```

```bash
cd rag-mcp && pip install -e ".[test]" && python -m pytest -q
```

| 환경 변수 | 서버 | 뜻 |
| --- | --- | --- |
| `INTERNAL_MODE` | business-mcp | `fake`(기본, 가짜 `/internal`) 또는 `http` |
| `BACKEND_URL`, `INTERNAL_TOKEN` | business-mcp | `http` 모드의 백엔드 주소와 서비스 토큰 |
| `RAG_DOCS_DIR` | rag-mcp | 근거 문서 폴더 (기본 `docs/rag/`) |
| `MCP_HOST`, `MCP_PORT` | 둘 다 | 기본 `0.0.0.0`, 9101 / 9102 |

테스트는 인메모리 Client(`Client(server)`)로 MCP 프로토콜을 그대로 거친다. 가짜 `/internal`의 대여소·재고는 가짜 값이다.

---

## 6. 열린 항목

| 항목 | 상대 | 내용 |
| --- | --- | --- |
| `get_audit` 계약 | 4 백엔드 | 내부 API `GET /internal/shifts/{shiftId}/audit`와 `AuditEvent` 추가 여부 (ISSUES ISS-06). 결정되면 harness.yaml `qa`에 다시 넣음 |
| 권역 ID 형식 | 6 데이터, 4 백엔드 | `N01`~`S09` 제안 (6번 agreements 2번). MCP는 대문자도 받음 |
| 근거 문서 위치·머리말 | 6 데이터 | 6번 제안은 `docs/evidence/`와 `title`·`doc_id`·`version`·`updated`·`source`·`status` 머리말 (agreements 21번). 지금은 `docs/rag/`와 4.3절 머리말. 합의 후 한쪽으로 맞춤. `rebal-summary`와 `analysis-reverse-estimation`이 겹침 |
| `Forecast` 시간별 값 | 6 데이터, 4 백엔드 | `rentalsH`·`returnsH` 추가 제안 (agreements 15번). 백엔드가 주면 MCP는 그대로 전달 |
| `horizonH` 최대값 | 4 백엔드, 6 데이터 | specs 6, MCP 5 (근거 문서 7절) |
| 임베딩 모델 | 1, 2 | 모델 이름·차원 기록 |
| compose 등록 | 5 인프라 | `business-mcp`, `rag-mcp` 컨테이너와 `docs/rag` 마운트 |
| 불꽃축제 폐쇄 대여소 목록 | 6 데이터 | 서울시 첨부 PDF를 대여소 ID와 맞춰 [notice-seoul-fireworks-2025](rag/notice-seoul-fireworks-2025.md) 5절 예시를 채움 |
