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
| ISS-02 | `propose_route_change` 승인이 도구 앞 승인과 기사 수락으로 겹침 | 대기 | 기사가 같은 변경을 두 번 승인하거나 관리자 승인을 기사가 모름 (FR-13) | 1, 4 | Harness는 specs 안 (나)로 구현 완료: 등급별 카드(MINOR 기사, MAJOR 관리자), 그 승인이 곧 수락, 승인자 불일치 거부. 1·4번 확정 대기 | HARNESS 1.6, SC-02, SC-08 |
| ISS-03 | 계약 `threadId`가 근무조 단위 | 결정 | 근무조 단위로 쓰면 같은 근무조 작업이 대화를 공유해 승인된 계획이 실행되지 않는 결함이 다시 생김 | 1, 4 | Harness는 `{shiftId}:{taskId}`를 내부 스레드로 쓴다. 계약 설명 수정 제안 | HARNESS 8절, 회귀 테스트 `test_tasks_on_same_shift_...` |
| ISS-04 | 계약 `requestedBy`에 기사 `truckId` 없음 | 결정 | 기사 범위 제한(NFR-06)에 필요. 현장 보고는 보고의 `truckId`로 대신할 수 있으나 질의 등 다른 작업은 불가 | 4 | `requestedBy.truckId` 추가 제안 | asyncapi `TaskCommand` |
| ISS-05 | 계약 `FAILED` 이벤트에 실패 코드 필드 없음 (`error` 문자열만) | 결정 | 작업 이력 화면·평가가 실패 사유를 코드로 집계 못 함 | 4 | `code` 필드 추가 제안. 그전까지 Harness는 확장 필드로 보냄 | openapi `TaskEvent` |
| ISS-06 | `get_audit` 도구가 계약·05 뷰에 없음 | 결정 | FR-08 "감사 로그 근거 질의"를 근거 있게 답하기 어려움 | 3, 4 | 내부 API·MCP 도구 추가 여부 결정. 그전까지 `qa`는 계획·현장 보고로 답함 | PRD 6.7 |
| ISS-07 | LLM이 맡는 범위: PRD(해석·설명만) vs 05 뷰(도구 선택) | 결정 | Harness 구조, 승인 위치, MCP 도구 구성이 달라짐 | 전원 | 현재 05 뷰·specs 방식으로 진행. 팀 확인 | HARNESS 8절 |
| ISS-08 | 실제 LLM 평가 기준선 없음 | 대기 | 모델 품질(시나리오 통과율, 설명 품질)을 모름 | 2 | 유효한 `OPENAI_API_KEY` 또는 Ollama 접근 확보 후 `python -m evaluation --model ...` | 현재 키 401 |
| ISS-09 | 배포 서버 Ollama(`:10443`) 접근 불가 | 대기 | 무료 실제 모델 평가 경로가 막힘 | 서버 담당 | 서버 Tailscale 네트워크에 이 맥 공유 요청 | 접속 시 응답 없음 |
| ISS-10 | 실제 LangSmith 계정에서 trace 미확인 | 대기 | 토큰·비용 집계, 화면 링크 동작 미확인 | 2 | `LANGSMITH_API_KEY` 확보 후 확인. 업로드 내용은 로컬 수신 서버로 확인함 | HARNESS 3.8 |
| ISS-11 | 배포 서버 API 미구현 (`/api/ai/tasks`, `/internal/**` 404) | 대기 | 실제 백엔드·MCP와 연결 테스트 불가 | 4 | 구현 후 연결 테스트 | 2026-10-20 배포 서버 확인 |
| ISS-12 | `field_report` 10초 한도(NFR-01)를 실제 모델로 지키는지 미확인 | 대기 | 넘으면 현장 보고가 `LOOP_TIMEOUT`으로 실패 | 2 | ISS-08 해결 후 측정. 어렵다면 메모 없는 보고는 LLM을 거치지 않게 (1번) | HARNESS 3.5 |
| ISS-13 | `agent/` 테스트가 CI에 없음 | 열림 | PR에서 Harness 회귀를 못 잡음 | 2, 5 | CI에 `agent` 잡 추가 요청 (`python -m unittest discover tests`, `python -m evaluation --out`) | PR #3 체크 4개에 agent 없음 |
| ISS-14 | 한글 브랜치 이름 `성훈`이 도구에서 `\uC131` 등으로 깨져 보임 | 결정 | 병합 커밋 제목, `revert-3-성훈` 등에서 읽기 어려움 | 2 | 영문 브랜치(예 `seonghun`)로 바꿀지 결정 | 팀장 지적 |
| ISS-15 | 원격에 `revert-3-성훈` 브랜치 존재 | 대기 | PR #3 되돌리기 여부 불명 | 팀장 | 팀장 판단 확인 | `git branch -r` |
| ISS-16 | `docs/ROLES.md` 역할 번호가 Slack 분담과 다름 | 열림 | 문서마다 역할 번호 해석이 다름 | 팀장 | ROLES 갱신 요청 | QUALITY 1.4 |
| ISS-17 | MCP 도구 인자 이름 규칙 미정 | 결정 | 3번 실제 서버와 Harness 검증 스키마·프롬프트가 다르면 모든 호출이 거부됨 | 3 | Harness 안: 내부 API 필드의 snake_case (`request_route_plan(shift_id, kind, truck_ids, exclude_station_ids)` 등), 변경 도구는 `approval_id`·`task_id` 인자를 받되 Harness가 채움. 3번 확인 | HARNESS 3.6, `evaluation/fake_mcp.py` |
| ISS-18 | 큰 변경(MAJOR) 뒤 기사 확인 단계 여부 | 결정 | openapi 설명 "큰 변경은 관리자가 먼저 승인"이 기사 추가 확인을 뜻하는지 불분명 | 4 | Harness는 관리자 승인만으로 적용하고 기사에게는 결과를 알린다. 4번 확인 | openapi `/approval` 설명 |

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
