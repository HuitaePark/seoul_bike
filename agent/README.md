# agent: Harness · 평가

| 항목 | 내용 |
| --- | --- |
| 담당 | 2 Harness · 평가 · 품질 |
| 명세 | [Harness 명세](../docs/HARNESS.md), [품질 매핑](../docs/QUALITY.md), [이슈 기록](../docs/ISSUES.md), [계약](../docs/specs/README.md) |
| 요구 | Python 3.12 이상, [uv](https://docs.astral.sh/uv/) |

agent 서버 안에서 모델을 감싸는 Harness 8요소와 그 평가셋이다. LangGraph 근무조 그래프(1 에이전트 그래프)는 이 패키지의 `HarnessAgent`를 호출한다 ([HARNESS 7.3](../docs/HARNESS.md#73-그래프-담당이-쓰는-법)).

## 구조

| 경로 | 내용 |
| --- | --- |
| `harness/` | Harness 8요소 (요소별 파일은 [HARNESS 7.1](../docs/HARNESS.md#71-파일)) |
| `graph/` | 근무조 그래프 (1 에이전트 그래프): `ai.tasks` → Harness → 승인 카드 → `ai.events`, LLM 없는 대체 경로, Kafka 일꾼 ([AGENT-GRAPH](../docs/AGENT-GRAPH.md)) |
| `harness.yaml` | 모든 한도와 설정값 |
| `prompts/` | 작업 종류별 프롬프트 (`version` 필수) |
| `evaluation/` | 시나리오 평가셋, 평가 전용 가짜 MCP 서버, 스크립트 모델, 채점, 실행기 |
| `tests/` | Harness 동작 테스트(`test_harness.py`), 근무조 그래프 테스트(`test_graph*.py`) |
| `e2e/` | 실제 backend·Kafka와 붙여 보는 통합 확인 (compose 덮어쓰기, 03a 스크립트) |

## 설치

```bash
cd agent
uv venv --python 3.12
uv pip install -e ".[dev]"   # dev: 계약 스키마 검사(jsonschema)
```

## 실행

반드시 `agent/` 폴더에서, 이 폴더의 `.venv` Python으로 실행한다. `python`이 다른 버전(예: Homebrew 3.11)이나 다른 프로젝트 가상환경을 가리키면 테스트를 못 찾거나(`Ran 0 tests`) 다른 프로젝트 테스트가 실행된다.

```bash
.venv/bin/python -m unittest discover tests -v      # 동작 테스트 (Harness 38 + 그래프 35), 키 불필요, 약 4초
.venv/bin/python -m unittest tests.test_harness.RecoveryTest.test_fallback_model_after_retries   # 한 줄로 입력
.venv/bin/python -m evaluation --out /tmp/eval.md   # 시나리오 15개, 키 불필요, 약 3초
```

| 명령 | 언제 |
| --- | --- |
| `python -m evaluation --out /tmp/eval.md` | 확인만 할 때 (결과를 저장소에 남기지 않음) |
| `python -m evaluation --label {변경}` | 결과를 `docs/eval/YYYY-MM-DD-{변경}.md`로 남길 때 |
| `python -m evaluation --model ollama:gemma4:31b --label {변경}` | 실제 모델 평가. `agent/.env`의 `OLLAMA_HOST`·`OLLAMA_API_KEY`를 먼저 읽는다 (`set -a; . ./.env; set +a`). 설명 품질 LLM 채점 포함 |
| `python -m evaluation --only SC-01,FT-05` | 일부 시나리오만 |

LangSmith에 기록하려면 `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`를 설정한다. 평가 trace는 `seoul-bike-eval` 프로젝트에 `harness:{task_type}` 이름으로 남는다.

## 테스트가 확인하는 것

모든 테스트는 실제 MCP 프로토콜(메모리 전송)로 연결한 가짜 MCP 서버와, 정해진 순서대로 답하는 스크립트 모델로 돈다. 가짜 서버는 `docs/specs/internal-api.yaml`의 도구·인자·결과 형식을 따르고, backend처럼 승인되지 않은 변경을 거부한다. 실제 LLM과 API 키가 없어도 매번 같은 결과가 나온다.

### 동작 테스트 (`tests/test_harness.py`)

| 묶음 | 테스트 | 깨지면 실패하는 동작 |
| --- | --- | --- |
| 승인 | `test_plan_runs_only_after_manager_approval` | 계획 제출은 관리자 승인 카드가 승인되기 전까지 실행되지 않고, 실행될 때 카드의 `approvalId`를 함께 보낸다 |
| | `test_route_change_cards_follow_required_approvers` | 경로 변경 카드는 후보의 `requiredApprovers` 순서를 따른다 (작은 변경 기사, 큰 변경 관리자 먼저) |
| | `test_major_change_needs_manager_then_driver_card` | 큰 변경은 관리자 승인 뒤 같은 후보의 기사 카드가 나오고, 기사 수락 뒤에만 적용된다 |
| | `test_manager_rejection_sends_no_driver_card` | 관리자가 거절하면 기사 카드를 보내지 않고 기존 경로를 유지한다 |
| | `test_wrong_approver_cannot_resume` | 카드의 승인자가 아니거나 승인 ID가 다른 RESUME으로는 아무것도 실행되지 않는다 (관리자 카드의 기사, 기사 카드의 관리자) |
| | `test_rejected_change_is_never_executed` | 거절하면 실행되지 않고 사유가 모델에 전달된다 |
| | `test_excluded_stations_send_the_plan_back_for_recompute` | 대여소 제외 요청은 검토한 계획을 활성화하지 않고 다시 계산해 새 카드를 만든다 (FR-04) |
| | `test_model_cannot_see_or_set_approval_id` | 모델은 `approval_id`를 볼 수도 넣을 수도 없다 |
| | `test_only_one_change_per_model_turn` | 한 응답에 변경 도구는 하나만 허용된다 |
| Guardrails | `test_unknown_station_in_change_is_sent_back_before_any_approval` | 조회한 적 없는 대여소로 변경하려 하면 승인 요청 전에 모델에게 되돌린다 |
| | `test_repeated_invalid_change_fails_task` | 같은 실수를 반복하면 `OUTPUT_INVALID`로 끝나고 아무것도 실행되지 않는다 |
| | `test_driver_identity_never_reaches_model_or_events` | 메모 속 기사 이름·전화번호와 트럭 좌표가 모델 입력과 이벤트 어디에도 없다 |
| | `test_read_only_task_never_gets_destructive_tools` | 질의 작업은 설정에 실수로 넣어도 변경 도구를 받지 않는다 |
| | `test_user_input_cannot_close_its_boundary` | 메모에 `</user_input>`을 넣어도 입력 경계를 벗어나지 못한다 |
| 인자 검증 | `test_inventory_value_needs_a_source` | 보고·조회·요청 어디에도 없는 재고 숫자는 거부된다 (NFR-07) |
| | `test_inventory_value_from_field_report_is_accepted` | 현장 보고에 있는 재고 숫자는 받아들여 승인 카드로 간다 |
| | `test_value_above_docks_is_rejected` | 거치대 수를 넘는 값은 거부된다 |
| | `test_unknown_constraint_type_is_rejected` | 계약에 없는 제약 유형은 거부된다 |
| | `test_only_computed_plans_can_be_submitted` | 계산 결과에 없는 계획 ID는 제출할 수 없다 |
| 기사 범위 | `test_driver_sees_recomputes_and_changes_only_own_truck` | 기사 작업에서 모델은 자기 트럭만 보고, 계산하고, 바꾼다 (NFR-06) |
| | `test_field_report_has_its_own_time_limit` | 현장 보고는 작업 종류별 시간 한도(NFR-01 10초)를 따른다 |
| Tools | `test_long_result_is_truncated_with_notice` | 긴 결과는 한도에서 잘리고 안내가 붙으며 이벤트에 기록된다 |
| | `test_tool_error_goes_back_to_model_with_retry_hint` | 없는 대여소 오류가 가까운 대여소 정보와 함께 모델에게 돌아간다 |
| | `test_rag_top_k_is_capped` | 문서 검색 `top_k`는 5로 제한된다 |
| Agent Loop | `test_turn_limit` | 턴 한도를 넘으면 `LOOP_MAX_TURNS` |
| | `test_same_call_is_blocked_then_fails` | 같은 도구·인자는 3번만 실행되고 5번째에 `LOOP_SAME_TOOL` |
| | `test_same_tool_with_different_arguments_is_allowed` | 인자가 다르면 같은 도구를 여러 번 불러도 정상 |
| | `test_segment_time_limit_cancels_slow_tool` | 시간 한도를 넘는 느린 도구를 끊고 `LOOP_TIMEOUT` |
| | `test_limits_restart_after_approval` | 승인 후 재개하면 한도가 새로 시작된다 |
| Recovery | `test_rate_limit_is_retried_with_backoff` | 429·503은 1초·4초 간격으로 재시도한다 |
| | `test_fallback_model_after_retries` | 재시도까지 실패하면 대체 모델이 답한다 |
| | `test_all_models_unavailable` | 대체 모델까지 실패하면 `MODEL_UNAVAILABLE` |
| | `test_bad_request_fails_without_retry_or_fallback` | 400은 재시도·대체 모델 없이 `MODEL_ERROR` |
| Instructions | `test_input_is_wrapped_and_brief_is_in_system_prompt` | 첫 메시지가 `요청자: …`로 시작하고 입력은 `<user_input>`으로 감싸진다 |
| State | `test_tasks_on_same_shift_do_not_share_history_or_lose_approvals` | 같은 근무조 작업끼리 대화를 공유하지 않고 승인된 계획은 실제로 실행된다 |
| 계약 | `test_every_event_type_matches_the_kafka_and_sse_contract` | 이벤트 6종이 `docs/specs`의 `TaskEventMessage` 스키마를 통과하고 `seq`는 보내지 않는다 (backend가 매김) |
| | `test_field_report_command_becomes_a_driver_task` | 계약 `TaskCommand`(FIELD_REPORT)가 기사 작업으로 바뀐다 (트럭은 보고에서, 입력은 메모에서) |
| Observability | `test_langsmith_runs_carry_trace_id_and_no_driver_identity` | LangSmith 업로드에 `trace_id`가 있고 기사 개인정보 원본은 없다 |

### 시나리오 평가 (`python -m evaluation`)

작업은 계약 `TaskCommand`로, 승인은 RESUME으로 넣어 agent 서버와 같은 경로로 업무 흐름 전체를 돌린다. 모든 시나리오에 공통 검사(결과, 무승인 변경 0건, 마스킹, 숫자 출처)와 시나리오별 검사를 한다. 목록과 채점 기준은 [HARNESS 6절](../docs/HARNESS.md#6-평가-evaluation)에 있다.

키 없이 돌리면 모든 시나리오가 기준 궤적(정답 순서)으로 움직이므로 결과는 "Harness와 채점이 제대로 돈다"는 확인이다. 모델 품질은 `--model`을 주고 실행해야 측정된다.

## 근무조 그래프 (1 에이전트 그래프)

명세와 설계 근거는 [AGENT-GRAPH](../docs/AGENT-GRAPH.md). 모델 없이도 계획 초안·메모 없는 현장 보고·승인이 돈다 (`AGENT_LLM=none`).

```bash
.venv/bin/python -m unittest tests.test_graph        # 그래프 35개, 키·브로커 불필요
.venv/bin/python -m graph                            # agent 서버: Kafka ai.tasks 소비 → ai.events 발행
```

| 환경 변수 | 기본 | 뜻 |
| --- | --- | --- |
| `KAFKA_BOOTSTRAP` | `localhost:9092` | 브로커 |
| `AGENT_PG_URL` | (없으면 메모리) | 체크포인터·처리 기록 PostgreSQL |
| `AGENT_LLM` | harness.yaml 모델 | `none`이면 LLM 없는 경로만, `ollama:<모델>`·`openai:<모델>`이면 그 모델 (서버는 `openai:gpt-4.1-mini`, `OPENAI_API_KEY` 필요) |
| `MCP_BUSINESS_URL` · `MCP_RAG_URL` | `http://business-mcp:9101/mcp` · `http://rag-mcp:9102/mcp` | MCP 서버 |
| `MCP_FAKE` | `0` | `1`이면 평가용 가짜 MCP (예시 데이터, backend는 바뀌지 않는다. 시연에는 쓰지 않는다) |

팀 compose에서는 `agent` 서비스로 뜬다 (`Dockerfile`, [infra/compose.yaml](../infra/compose.yaml)). `docker compose up`이 backend·redpanda·PostgreSQL과 함께 띄우고, 위 변수는 compose가 넣는다: 브로커 `redpanda:9092`, PostgreSQL은 `agent` 데이터베이스(`agent-db-init`이 만든다), `MCP_FAKE`는 기본 `0`(실제 business-mcp·rag-mcp), `AGENT_LLM`·`OPENAI_API_KEY`·`OLLAMA_*`·`MCP_*`는 `infra/.env`에서 읽는다. healthcheck는 agent가 MCP·Kafka에 붙은 뒤 갱신하는 `/tmp/agent-ready`를 본다. 이때 backend의 `FAKE_AGENT_ENABLED`는 `false`로 둔다 (켜 두면 둘 다 답한다).

```bash
docker compose -f ../infra/compose.yaml --env-file ../infra/.env up -d --build agent   # agent와 그 의존 서비스
docker compose -f ../infra/compose.yaml --env-file ../infra/.env logs -f agent
```

실제 backend·Kafka·화면과 붙여 보는 방법은 [AGENT-GRAPH 8.2](../docs/AGENT-GRAPH.md#82-통합-확인-실제-backend--kafka--화면)에 있다.
