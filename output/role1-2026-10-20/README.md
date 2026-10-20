<!--
Author: Jung Minkyu
Date: 20OCT2026
Purpose: 1 에이전트 그래프 검증 결과물 모음. 무엇을 돌렸고, 결과가 어디 있고, 어떻게 읽는지.
-->

# 1 에이전트 그래프 검증 결과 (2026-10-20)

진행 상황과 확인 방법 설명은 [docs/roles/01-status.md](../../docs/roles/01-status.md), 설계와 근거는 [docs/AGENT-GRAPH.md](../../docs/AGENT-GRAPH.md)에 있다. 이 폴더는 그 확인을 실제로 돌린 **원본 출력, 화면, 재현 스크립트**다. 실행 환경은 [ENVIRONMENT.md](ENVIRONMENT.md).

## 한눈에 보기

| # | 확인 | 결과 | 원본 |
| --- | --- | --- | --- |
| 1 | 그래프 단위 테스트 | **통과** 31/31 | [logs/01](logs/01-graph-unit-tests.txt) |
| 2 | agent 전체 테스트 + PostgreSQL 재개 | 70개 중 **69 통과**. 실패 1개는 2 Harness 테스트(`test_field_report_command_becomes_a_driver_task`, main 그대로의 상태) | [logs/02](logs/02-all-agent-tests-with-postgres.txt) |
| 3 | Harness 평가셋 (스크립트 모델) | **15/15** | [logs/03](logs/03-harness-eval-scripted.md) |
| 4 | 화면 빌드, API 타입 검사(CI와 같음) | **통과** | [logs/04](logs/04-frontend-build.txt) |
| 5 | 03a 계획 승인, 실제 backend + Kafka + agent 컨테이너 | **통과** `E2E 03a OK` | [logs/05](logs/05-e2e-03a-backend.txt), 화면 01~04 |
| 6 | 03b 경로 변경 승인 순서 (큰 변경: 관리자 → 기사, 작은 변경: 기사 한 장) | **통과**. 다른 사람이 누르면 403, 실제 실행 `ok=true`(backend DB) | [logs/06](logs/06-e2e-03b-route-change.txt), 화면 05~08 |
| 7 | 실패도 이유와 함께 끝나는지 | **통과**. backend 작업 API로 만든 현장 보고 → `FAILED code=INTERNAL_ERROR`(G13), 모델 없는 질의 → `MODEL_UNAVAILABLE` | [logs/07](logs/07-e2e-failures-end-cleanly.txt) |
| 8 | 3 MCP 브랜치 자체 테스트 | **통과** business-mcp 29, rag-mcp 13 | [logs/08](logs/08-mcp-branch-selftests.txt) |
| 9 | 그래프 ↔ 실제 MCP 서버 | **통과**. 도구 12개 발견, 승인 기록 없으면 403 → FAILED(거짓 '실행' 없음), 있으면 실행 → DONE | [logs/09](logs/09-graph-vs-real-mcp.txt) |
| 10 | 실제 backend + agent 컨테이너 + 실제 MCP | **통과** (A: 403 → FAILED, B: 실행 → DONE) | [logs/10](logs/10-e2e-backend-real-mcp.txt) |
| 11 | 실제 모델(`gemma4:31b`) + 실제 MCP | **통과**. 질의 2.0초, 문서 근거 실림. 계획 초안 → 승인 → 실행 4.8초 | [logs/11](logs/11-real-model-real-mcp.txt) |

## 화면 (관제 작업 화면, 승인 카드)

| 화면 | 보이는 것 |
| --- | --- |
| [01-03a-plan-card](screenshots/01-03a-plan-card.jpg) | 계획 초안 → 승인 카드. 포기 사유, 트럭별 경로, 진행 이벤트 로그 |
| [02-03a-exclude-station](screenshots/02-03a-exclude-station.jpg) | '일부 제외'에서 ST-2005를 눌러 뺀 상태 ("1곳 빼고 다시 계산") |
| [03-03a-new-card-after-exclude](screenshots/03-03a-new-card-after-exclude.jpg) | 다시 계산한 새 카드 P-4. "제외한 대여소: ST-2005", 경로에서 빠짐 |
| [04-03a-approved-done](screenshots/04-03a-approved-done.jpg) | 승인 → 계획 활성화 실행 → 완료, 근거, 이벤트 9건 |
| [05-03b-major-step1-manager](screenshots/05-03b-major-step1-manager.jpg) | 큰 경로 변경 1/2 관리자 승인. "관리자 승인 → 기사 수락" 단계, 경로 차이(빠지는 곳 취소선) |
| [06-03b-major-step2-waiting-driver](screenshots/06-03b-major-step2-waiting-driver.jpg) | 관리자 승인 뒤 같은 작업에 2/2 기사 수락 카드. 관리자에게는 버튼 없이 "TR-1 기사가 필요한 카드" |
| [07-03b-major-step2-driver-view](screenshots/07-03b-major-step2-driver-view.jpg) | TR-1 기사로 로그인한 화면. '수락' 버튼, 자기 트럭 작업만 보임 |
| [08-03b-driver-accepted-done](screenshots/08-03b-driver-accepted-done.jpg) | 기사 수락 → 경로 변경 적용(✓) → 완료 |

## 읽을 때 알아 둘 것

- **"실행했다"는 답변 문장이 아니라 `TOOL_END`의 `ok`와 backend DB로 판단했다.** (예전에 도구 오류를 성공으로 읽던 문제를 고쳤다, ISS-21)
- **03b는 backend 작업 API로 시작할 수 없어서(4의 `/api/reports` 없음, G13)**, backend가 고친 뒤 보낼 모양의 명령을 Kafka에 직접 넣어 확인했다 (`scripts/e2e_03b_twostep.py`). 승인·403 확인은 모두 실제 backend API로 했다.
- **실제 MCP는 3의 브랜치 `유은/mcp`(main 머지 전)**를 그대로 띄웠다. backend `/internal`(M3)이 없어 business-mcp는 가짜 내부 API로 돌고, 승인 기록이 필요한 확인은 `scripts/mcp_with_approvals.py`가 backend의 승인 기록을 흉내 냈다.
- 화면의 기사 계정(`e2e-tr1-…`)과 비밀번호는 이 확인을 위해 로컬 스택에서 만든 일회용 값이고, 스택과 함께 지웠다. 이 폴더에 비밀값은 없다.

## 다시 돌리려면

`docs/roles/01-status.md` 3절의 1~5단계를 따른다. `scripts/`는 그때 쓴 스크립트 그대로다:
`check_03a.py`(03a), `e2e_03b_twostep.py`(03b, Kafka에 명령 넣기 포함), `e2e_real_mcp.py`(backend + 실제 MCP), `probe_real_mcp.py`·`probe_model_real_mcp.py`(그래프 ↔ 실제 MCP, 모델 없음·실제 모델), `mcp_with_approvals.py`(승인 기록을 흉내 내는 business-mcp, 3 브랜치의 MCP 가상환경에서 실행).
