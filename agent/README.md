# agent: Harness · 평가

| 항목 | 내용 |
| --- | --- |
| 담당 | 2 Harness · 평가 · 품질 |
| 명세 | [Harness 명세](../docs/HARNESS.md), [품질 매핑](../docs/QUALITY.md) |
| 요구 | Python 3.12 이상, [uv](https://docs.astral.sh/uv/) |

agent 서버 안에서 모델을 감싸는 Harness 8요소와 그 평가셋이다. LangGraph 근무조 그래프(1 에이전트 그래프)는 이 패키지의 `HarnessAgent`를 호출한다 ([HARNESS 7.3](../docs/HARNESS.md#73-그래프-담당이-쓰는-법)).

## 구조

| 경로 | 내용 |
| --- | --- |
| `harness/` | Harness 8요소 (요소별 파일은 [HARNESS 7.1](../docs/HARNESS.md#71-파일)) |
| `harness.yaml` | 모든 한도와 설정값 |
| `prompts/` | 작업 종류별 프롬프트 (`version` 필수) |
| `evaluation/` | 시나리오 평가셋, 평가 전용 가짜 MCP 서버, 스크립트 모델, 채점, 실행기 |
| `tests/` | Harness 동작 테스트 |

## 설치

```bash
cd agent
uv venv --python 3.12
uv pip install -e .
```

## 실행

반드시 `agent/` 폴더에서, 이 폴더의 `.venv` Python으로 실행한다. `python`이 다른 버전(예: Homebrew 3.11)이나 다른 프로젝트 가상환경을 가리키면 테스트를 못 찾거나(`Ran 0 tests`) 다른 프로젝트 테스트가 실행된다.

```bash
.venv/bin/python -m unittest discover tests -v      # 동작 테스트 27개, 키 불필요, 약 3초
.venv/bin/python -m unittest tests.test_harness.RecoveryTest.test_fallback_model_after_retries   # 한 줄로 입력
.venv/bin/python -m evaluation --out /tmp/eval.md   # 시나리오 14개, 키 불필요, 약 4초
```

| 명령 | 언제 |
| --- | --- |
| `python -m evaluation --out /tmp/eval.md` | 확인만 할 때 (결과를 저장소에 남기지 않음) |
| `python -m evaluation --label {변경}` | 결과를 `docs/eval/YYYY-MM-DD-{변경}.md`로 남길 때 |
| `python -m evaluation --model openai:gpt-4.1-mini --label {변경}` | 실제 모델 평가. `OPENAI_API_KEY` 필요. 설명 품질 LLM 채점 포함 |
| `python -m evaluation --only SC-01,FT-05` | 일부 시나리오만 |

LangSmith에 기록하려면 `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`를 설정한다. 평가 trace는 `seoul-bike-eval` 프로젝트에 `harness:{task_type}` 이름으로 남는다.

## 테스트가 확인하는 것

모든 테스트는 실제 MCP 프로토콜(메모리 전송)로 연결한 가짜 MCP 서버와, 정해진 순서대로 답하는 스크립트 모델로 돈다. 실제 LLM과 API 키가 없어도 매번 같은 결과가 나온다.

### 동작 테스트 (`tests/test_harness.py`)

| 묶음 | 테스트 | 깨지면 실패하는 동작 |
| --- | --- | --- |
| Guardrails | `test_destructive_tool_runs_only_after_approval` | 변경 도구는 승인 전까지 실행되지 않고 승인 대기로 멈춘다. 승인 후에만 실행되고, 모든 이벤트의 `trace_id`가 같다 |
| | `test_rejected_change_is_never_executed` | 거절하면 실행되지 않고 `rejected=true`로 끝나며, 거절 사유가 모델에 전달된다 |
| | `test_unknown_station_in_change_is_sent_back_before_any_approval` | 조회한 적 없는 대여소 ID로 변경을 시도하면 승인 요청 전에 모델에게 되돌려 다시 시킨다 |
| | `test_repeated_invalid_change_fails_task` | 같은 실수를 반복하면 `OUTPUT_INVALID`로 끝나고 아무것도 실행되지 않는다 |
| | `test_driver_identity_never_reaches_model_or_events` | 기사 이름·전화번호·좌표가 모델 입력과 이벤트 어디에도 없다 (`기사#xxxx`로 대체) |
| | `test_read_only_task_never_gets_destructive_tools` | 질의(`qa`) 작업은 설정에 실수로 넣어도 변경 도구를 받지 않는다 |
| | `test_user_input_cannot_close_its_boundary` | 기사 메모에 `</user_input>`을 넣어도 입력 경계를 벗어나지 못한다 |
| Tools | `test_long_result_is_truncated_with_notice` | 긴 결과는 한도에서 잘리고 `[잘림: ...]` 안내가 붙으며 이벤트에 기록된다 |
| | `test_tool_error_goes_back_to_model_with_retry_hint` | 없는 대여소 오류가 작업을 죽이지 않고 가까운 대여소 정보와 함께 모델에게 돌아간다 |
| | `test_rag_top_k_is_capped` | 문서 검색을 `top_k=50`으로 불러도 5개로 제한된다 |
| Agent Loop | `test_turn_limit` | 턴 한도를 넘으면 `LOOP_MAX_TURNS` |
| | `test_same_call_is_blocked_then_fails` | 같은 도구·인자는 3번만 실행되고, 4번째는 경고, 5번째는 `LOOP_SAME_TOOL` |
| | `test_same_tool_with_different_arguments_is_allowed` | 인자가 다르면 같은 도구를 여러 번 불러도 정상 |
| | `test_segment_time_limit_cancels_slow_tool` | 시간 한도를 넘는 느린 도구를 끊고 `LOOP_TIMEOUT` |
| | `test_limits_restart_after_approval` | 승인 후 재개하면 한도가 새로 시작된다 (승인 대기 시간 제외) |
| Recovery | `test_rate_limit_is_retried_with_backoff` | 429·503은 1초·4초 간격으로 재시도하고, 성공하면 대체 모델을 쓰지 않는다 |
| | `test_fallback_model_after_retries` | 재시도까지 실패하면 대체 모델이 답하고 `fallback=true`가 남는다 |
| | `test_all_models_unavailable` | 대체 모델까지 실패하면 `MODEL_UNAVAILABLE` |
| | `test_bad_request_fails_without_retry_or_fallback` | 400 같은 요청 오류는 재시도·대체 모델 없이 `MODEL_ERROR` |
| Instructions | `test_input_is_wrapped_and_brief_is_in_system_prompt` | 첫 메시지가 `요청자: …`로 시작하고 사용자 입력은 `<user_input>`으로 감싸지며, 근무조 상태 요약이 시스템 프롬프트에 들어간다 |
| State | `test_tasks_on_same_shift_do_not_share_history_or_lose_approvals` | 같은 근무조에서 계획이 승인 대기 중일 때 기사 보고가 와도, 기사 작업은 계획 대화를 보지 못하고 계획은 승인 후 실제로 실행된다 |
| 인자 검증 | `test_bike_count_must_come_from_report` | 메모 숫자(5대)로 만든 재고 제약은 보고 값(3대)과 달라 거부되고, 반복하면 `OUTPUT_INVALID`로 끝나며 재계산은 실행되지 않는다 |
| | `test_bike_count_above_dock_capacity_is_rejected` | 거치대 수(20대)를 넘는 재고 제약은 거부된다 |
| | `test_unknown_constraint_shape_is_rejected` | 정의되지 않은 제약 형식은 거부되고, 고친 호출만 실행된다 |
| 기사 범위 | `test_driver_sees_and_changes_only_own_truck` | 기사 작업에서 모델은 자기 트럭 정보만 보고, 다른 트럭 경로 제안은 거부된다 |
| | `test_field_report_has_its_own_time_limit` | 현장 보고는 작업 종류별 시간 한도(NFR-01 10초)를 따른다 |
| Observability | `test_langsmith_runs_carry_trace_id_and_no_driver_identity` | LangSmith로 올라가는 run에 `trace_id` 메타데이터가 있고, 기사 이름·전화번호·좌표 원본은 하나도 없다 (업로드를 받아 두는 로컬 서버로 확인) |

### 시나리오 평가 (`python -m evaluation`)

업무 흐름 전체를 돌리고, 모든 시나리오에 공통 검사(결과 상태, 무승인 변경 0건, 마스킹 노출 0건, 답변 숫자의 출처)와 시나리오별 검사를 한다. 시나리오 목록과 채점 기준은 [HARNESS 6절](../docs/HARNESS.md#6-평가-evaluation)에 있다.

키 없이 돌리면 모든 시나리오가 기준 궤적(정답 순서)으로 움직이므로 결과는 "Harness와 채점이 제대로 돈다"는 확인이다. 모델 품질은 `--model`을 주고 실행해야 측정된다.
