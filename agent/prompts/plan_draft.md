---
version: 3
---
## 작업: 근무조 계획 초안
1. `business_get_risk_stations`로 위험 대여소를 확인한다.
2. `rag_search_docs`로 오늘 권역의 행사·공사·대여 중지 공지를 찾는다. 해당 공지가 있으면 그 대여소를 `exclude_station_ids`에 넣는다.
3. `business_request_route_plan(kind="PLAN")`으로 경로를 계산한다.
4. 결과의 `plan.planId`로 `business_submit_plan`을 불러 관리자 승인을 요청한다.
5. 관리자가 대여소를 빼라며 거절하면 그 대여소를 `exclude_station_ids`에 더해 3단계부터 다시 하고, 새 `planId`로 다시 승인을 요청한다.
6. 설명을 남긴다: 트럭별 경로 요약, 포기 대여소와 사유(계산 결과의 `unserved` 그대로), 반영한 공지와 제외 대여소.
