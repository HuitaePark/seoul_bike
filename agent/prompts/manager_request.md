---
version: 2
---
## 작업: 관리자 상황 반영 요청
1. `rag_search_docs`로 요청과 관련된 공지·운영 기준을 찾는다. 찾은 문서의 `doc://` 출처를 기억한다.
2. 영향받는 대여소를 문서와 조회 도구 결과에서 확인한다. 문서나 도구 결과에 없는 대여소는 넣지 않는다.
3. 재고를 고치라는 요청이면 `business_get_field_reports`나 `business_get_station_detail`로 근거가 되는 재고를 먼저 확인한다.
4. `business_add_constraint`로 제약을 요청한다. `reason`에 근거 문서나 보고를 적는다.
5. 승인되면 `business_request_route_plan(kind="REPLAN")`으로 다시 계산하고 결과를 요약한다.
