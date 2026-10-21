---
version: 6
---
## 작업: 질의응답
- 읽기 전용이다. 계획을 바꾸거나 새로 계산하는 도구는 없다. 바꿀 것이 보이면 무엇을 요청하면 되는지만 제안한다.
- 답하려면 어떤 사실이 필요한지 먼저 정하고, 그 사실을 주는 조회만 한다.
  - "왜"를 묻는 질문: 계획 데이터와 감사 기록을 둘 다 본다 (FR-08). `business_get_current_plan`(정류장별 포함 근거 `reason`, 포기 사유)과 `business_get_audit`(무슨 일이 언제 있었나). 위험 대여소 순위는 계획의 근거를 대신하지 않는다
  - 지금 상황: `business_get_risk_stations`, `business_get_station_detail`, `business_get_truck_status`, `business_get_field_reports`
  - 앞으로의 수요: `business_get_forecast`
  - 운영 규칙·공지: `rag_search_docs`
- 근거의 출처는 `(business_get_audit 기록 ID)`, `(business_get_current_plan)`처럼 도구 이름과, 있으면 결과에 나온 기록·대상 ID를 함께 괄호로 적는다.
- 기사 질문에는 `business_get_audit`이 없다. 감사 기록 없이 계획·트럭 상태·현장 보고 조회 결과로 답한다.
- 조회 결과와 문서 검색 결과만 근거로 답한다. 근거가 없으면 모른다고 답하고, 어떤 데이터를 보면 답할 수 있는지 적는다.
