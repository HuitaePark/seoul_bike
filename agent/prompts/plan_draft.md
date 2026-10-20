---
version: 2
---
## 작업: 근무조 계획 초안
1. `business_get_risk_stations`로 위험 거점을 확인한다.
2. `rag_search_docs`로 오늘 권역의 행사·공사·대여 중지 공지를 찾는다. 해당 공지가 있으면 `suspended` 제약 조건으로 넘긴다.
3. `business_request_route_plan`으로 경로를 계산한다. 계산 결과의 계획 ID를 그대로 쓴다.
4. `business_submit_plan`으로 관리자 승인을 요청한다.
5. 관리자가 거점을 빼라며 거절하면 그 거점을 `excluded` 제약으로 넣어 3단계부터 다시 하고, 새 계획 ID로 다시 승인을 요청한다.
6. 승인 결과와 함께 설명을 남긴다: 트럭별 경로 요약, 포기 거점과 사유(계산 결과의 `unserved` 그대로), 반영한 공지와 제외 거점.
