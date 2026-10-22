---
version: 3
---
## 작업: 관리자 상황 반영 요청
목표: 관리자가 알려 준 상황(행사·공사·대여 중지·재고 차이 등)을 근거와 함께 구조화된 제약으로 바꿔 관리자 확인을 받고, 승인되면 그 제약으로 다시 계산한다 (FR-09).

판단할 것
- 근거: 공지·운영 기준이 걸린 요청이면 `rag_search_docs`로 문서를 찾고, 그 `doc://` 출처를 제약 `reason`에 적는다. 재고를 고치라는 요청이면 `business_get_field_reports`·`business_get_station_detail`로 근거가 되는 재고를 먼저 확인한다.
- 대상 대여소: 문서나 조회 결과에 나온 대여소만 쓴다. 이름만 있으면 조회로 ID를 확인한다.
- 제약 유형과 기간: 문서·요청에 적힌 시각을 쓴다. 대여·반납 중지는 `STATION_SUSPENDED`, 트럭 진입 불가는 `STATION_BLOCKED`, 실제 재고는 `INVENTORY_OVERRIDE`, 목표 가감은 `TARGET_ADJUST`, 우선 처리는 `PRIORITY_BOOST`.

끝나는 조건
- `business_add_constraint`로 제약을 제안했다.
- 제약이 승인되어 저장되면(같은 작업 안에서 이어진다) `business_request_route_plan(kind="REPLAN")`으로 다시 계산하고, 바뀌는 트럭과 정류장을 계산 결과에서 인용해 요약했다. 제약 저장만으로는 지금 경로가 바뀌지 않는다.
- 관리자가 바뀐 경로를 적용하라고 했다면, 바뀐 트럭마다 `business_propose_route_change`로 적용을 제안한다(기사 수락이 필요하다). 적용하라는 말이 없으면 요약까지 한다.
- 제약이 거절되면 기존 계획을 유지하고 사유를 정리한다.
