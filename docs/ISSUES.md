# 이슈 기록: Harness · 평가 · 품질

| 항목 | 내용 |
| --- | --- |
| 담당 | 2 Harness · 평가 · 품질 |
| 작성일 | 2026-10-20 |
| 관련 문서 | [Harness 명세](HARNESS.md), [품질 매핑](QUALITY.md), [계약](specs/README.md) |

> 작업 중 발견한 문제, 막힌 것, 팀 결정이 필요한 것을 모두 여기에 남긴다. 새 이슈는 **열린 이슈** 표 맨 아래에 번호를 이어 붙이고, 해결하면 **해결된 이슈**로 옮겨 해결 방법과 커밋을 적는다.

상태: `열림` 손대기 전 · `진행` 작업 중 · `대기` 다른 사람·외부 조건 필요 · `결정` 팀 결정 필요

---

## 열린 이슈

| ID | 제목 | 상태 | 영향 | 담당 | 다음 행동 | 근거 |
| --- | --- | --- | --- | --- | --- | --- |
| ISS-07 | LLM이 맡는 범위: PRD(해석·설명만) vs 05 뷰(도구 선택) | 결정 | Harness 구조, 승인 위치, MCP 도구 구성이 달라짐 | 전원 | 현재 05 뷰·specs 방식으로 진행. 팀 확인 | HARNESS 8절 |
| ISS-08 | 실제 LLM 평가 기준선 없음 | 대기 | 모델 품질(시나리오 통과율, 설명 품질)을 모름 | 2 | 유효한 `OPENAI_API_KEY` 또는 Ollama 접근 확보 후 `python -m evaluation --model ...` | 현재 키 401 |
| ISS-09 | 배포 서버 Ollama(`:10443`) 접근 불가 | 대기 | 무료 실제 모델 평가 경로가 막힘 | 서버 담당 | 서버 Tailscale 네트워크에 이 맥 공유 요청 | 접속 시 응답 없음 |
| ISS-10 | 실제 LangSmith 계정에서 trace 미확인 | 대기 | 토큰·비용 집계, 화면 링크 동작 미확인 | 2 | `LANGSMITH_API_KEY` 확보 후 확인. 업로드 내용은 로컬 수신 서버로 확인함 | HARNESS 3.8 |
| ISS-11 | 실제 backend·MCP와 연결 테스트 미실시 | 대기 | Harness가 실제 서버와 맞물리는지 모름 | 2, 3, 4 | business-mcp 코드가 main에 들어옴. compose로 backend·business-mcp를 띄우거나 테스트 계정·MCP 주소를 받아 `harness.connect`로 연결 테스트 | 2026-10-21 main 확인 |
| ISS-12 | `field_report` 10초 한도(NFR-01)를 실제 모델로 지키는지 미확인 | 대기 | 넘으면 현장 보고가 `LOOP_TIMEOUT`으로 실패 | 2 | ISS-08 해결 후 측정. 어렵다면 메모 없는 보고는 LLM을 거치지 않게 (1번) | HARNESS 3.5 |
| ISS-13 | `agent/` 테스트가 CI에 없음 | 열림 | PR에서 Harness 회귀를 못 잡음 | 2, 5 | CI에 `agent` 잡 추가 요청 (`python -m unittest discover tests`, `python -m evaluation --out`) | PR #3 체크 4개에 agent 없음 |
| ISS-14 | 한글 브랜치 이름 `성훈`이 도구에서 `\uC131` 등으로 깨져 보임 | 결정 | 병합 커밋 제목, `revert-3-성훈` 등에서 읽기 어려움 | 2 | 영문 브랜치(예 `seonghun`)로 바꿀지 결정 | 팀장 지적 |
| ISS-16 | `docs/ROLES.md` 역할 번호가 Slack 분담과 다름 | 열림 | 문서마다 역할 번호 해석이 다름 | 팀장 | ROLES 갱신 요청 | QUALITY 1.4 |
| ISS-19 | 큰 변경의 카드 단계(관리자 카드 승인 뒤 기사 카드 대기)가 프로세스 메모리에만 있음 | 열림 | 관리자 승인 뒤 agent가 재시작되면 Harness가 다시 관리자 카드를 기다린다. 변경이 무승인으로 실행되지는 않음 (backend가 순서 전체 확인) | 1, 2 | 단계 정보를 체크포인트에 저장하거나 RESUME에 담아 보내도록 1·4번과 결정 | `HarnessAgent._steps`, HARNESS 3.4 |
| ISS-20 | 평가용 가짜 MCP 응답 형식이 실제 business-mcp와 다름 | 열림 | 실제 서버는 `{source, events[], truncated, total}`처럼 감싼 객체를 돌려주는데 가짜는 목록을 그대로 돌려준다. Harness 처리는 둘 다 되지만 평가가 실제와 다른 입력으로 돈다 | 2 | 가짜 서버 응답을 실제 형식으로 맞추거나, 실제 business-mcp를 가짜 backend에 붙여 평가 | main `business-mcp/src/business_mcp/server.py` |

---

## 해결된 이슈

| ID | 제목 | 해결 | 커밋 |
| --- | --- | --- | --- |
| ISS-R1 | 같은 근무조 작업이 대화를 공유해 승인된 계획이 실행되지 않음 | 작업 단위 스레드 `{shiftId}:{taskId}`, 회귀 테스트 | `479ab53` |
| ISS-R2 | 재고 숫자를 모델이 만듦 (NFR-07), 인자 스키마·범위 검증 없음 | 인자 검증 모듈, 보고 값만 허용 | `479ab53` |
| ISS-R3 | 기사 작업에서 다른 트럭 정보가 모델에 보임 (NFR-06) | 결과 범위 제한, 제안 대상 검증 | `479ab53` |
| ISS-R4 | LangSmith 업로드에 기사 이름·전화번호·좌표 원본이 남음 | LangSmith `anonymizer`에 마스커 연결, 회귀 테스트 | `aa26f39` |
| ISS-R5 | PR·결과표에 팀 문서에 없는 기호(체크·엑스·긴 대시·부등호) | 일반 표기로 바꾸고 결과표 생성 코드 수정 | `35576aa` |
| ISS-R6 | Harness 진행 이벤트가 계약(`TaskEvent`·`ai.events`) 형식과 다름 (구 ISS-01) | `TaskEventMessage` 형식으로 내보내고 이벤트 6종을 `docs/specs` 스키마로 자동 검사 | `11391e1` |
| ISS-R7 | `propose_route_change` 승인이 도구 앞 승인과 기사 수락으로 겹침 (구 ISS-02) | 승인 방식 (나) 확정 (specs v0.3, PR #6): 기사 카드 승인이 곧 수락. Harness는 후보 `requiredApprovers` 순서로 카드를 보냄 | `15152b6` |
| ISS-R8 | 큰 변경(MAJOR) 뒤 기사 확인 단계 여부 (구 ISS-18) | specs v0.3에서 관리자 → 기사 카드 두 장으로 확정, 앞 카드 거절 시 중단. SC-08, 테스트 2개 | `15152b6` |
| ISS-R9 | 원격 `revert-3-성훈` 브랜치 (구 ISS-15) | 원격에서 삭제됨. PR #3은 main에 유지 | - |
| ISS-R10 | 계약 `threadId`가 근무조 단위 (구 ISS-03) | asyncapi v0.3에서 작업 단위 `{shiftId}:{taskId}`로 변경 | `0fd5ad6` (main) |
| ISS-R11 | 계약 `requestedBy`에 기사 `truckId` 없음 (구 ISS-04) | asyncapi v0.3 `requestedBy.truckId`. Harness는 이 값으로 기사 범위 한정 | `0fd5ad6` (main) |
| ISS-R12 | `FAILED` 이벤트에 실패 코드 없음 (구 ISS-05) | 계약 `code` 필드. Harness가 `FAILED.code`로 발행 | `4157927` |
| ISS-R13 | `get_audit` 도구 없음 (구 ISS-06) | internal-api v0.5 `GET /internal/shifts/{shiftId}/audit`, business-mcp `get_audit`. `qa` 허용 목록에 추가, SC-07 | `4157927` |
| ISS-R14 | MCP 도구 인자 이름 규칙 (구 ISS-17) | 실제 business-mcp가 Harness와 같은 snake_case 인자, `approval_id`·`task_id`는 Harness가 채우는 인자로 구현됨 | main `business-mcp/` |
