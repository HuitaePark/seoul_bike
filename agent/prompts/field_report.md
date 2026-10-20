---
version: 3
---
## 작업: 현장 보고 반영과 재경로
현장 보고(실제 재고, 싣기·내리기 대수)는 이미 백엔드에 기록되어 있고, 경로 계산에 자동으로 반영된다. 너는 메모를 해석하고 재경로를 요청한다.

1. 메모에 다른 대여소가 적혀 있으면 `business_get_station_detail`로 확인한다. 없는 대여소라는 오류가 오면 오류에 담긴 가까운 대여소 중에서 메모 내용과 맞는 것을 고른다.
2. 메모나 `보고 데이터`의 사정이 진입 불가·공사이면 그 대여소를 `exclude_station_ids`에 넣는다.
3. `business_request_route_plan(kind="REPLAN", truck_ids=[요청자 트럭])`으로 요청자 트럭의 남은 경로를 다시 계산한다.
4. 결과 `grade`가 `NONE`이면 바뀐 것이 없다고 알리고 끝낸다. 아니면 `business_propose_route_change(truck_id=요청자 트럭, candidate_id=결과의 candidateId)`로 적용을 요청한다.
5. 바뀐 정류장과 싣기·내리기 대수를 계산 결과에서 인용해 짧게 알린다.
