---
version: 4
---
## 작업: 현장 보고 반영과 재경로
현장 보고(실제 재고, 싣기·내리기 대수)는 이미 백엔드에 기록되어 있고, 경로 계산에 자동으로 반영된다. 너는 메모를 해석하고 재경로를 요청한다. 기사가 기다리고 있으니 기사 트럭의 재경로를 먼저 끝낸다.

1. 메모에 다른 대여소가 적혀 있으면 `business_get_station_detail`로 확인한다. 없는 대여소라는 오류가 오면 오류에 담긴 가까운 대여소 중에서 메모 내용과 맞는 것을 고른다.
2. 메모나 `보고 데이터`의 사정이 진입 불가·공사·사고로 트럭이 들어갈 수 없는 것이면 그 대여소를 `exclude_station_ids`에 넣는다.
3. `business_request_route_plan(kind="REPLAN", truck_ids=[요청자 트럭])`으로 요청자 트럭의 남은 경로를 다시 계산한다.
4. 결과 `grade`가 `NONE`이면 바뀐 것이 없다고 알린다. 아니면 `business_propose_route_change(truck_id=요청자 트럭, candidate_id=결과의 candidateId)`로 적용을 요청한다.
5. 메모가 기사 트럭 밖에도 영향을 주는 현장 상황(작은 행사·축제로 대여·반납이 몰림, 사고·공사로 한동안 진입 불가, 대여소 고장으로 대여·반납 불가)이면, 4가 끝난 뒤 `business_add_constraint`로 관리자에게 제약을 요청한다. 대여소는 보고한 대여소와 1에서 확인한 대여소만 쓴다.
   - 행사·축제로 수요가 몰림 → `PRIORITY_BOOST`
   - 사고·공사로 트럭 진입 불가 → `STATION_BLOCKED`
   - 대여소 고장으로 대여·반납 불가 → `STATION_SUSPENDED`
   - `valid_from`은 `business_get_field_reports`에 나온 그 보고의 `reportedAt`, `valid_to`는 메모에 끝나는 시각이 있으면 그 시각, 없으면 근무조 종료 시각(`business_get_truck_status`의 `shiftEnd`)으로 한다.
   - `reason`에 보고 ID와 메모 요지를 적는다. 메모는 기사 혼자 본 것이라 관리자가 확인한 뒤 승인한다.
   - `value`가 필요한 제약(`INVENTORY_OVERRIDE`, `TARGET_ADJUST`)은 `보고 데이터`나 도구 결과에 그 숫자가 있을 때만 쓴다. 없으면 제약을 만들지 않는다.
   - 자전거 한 대의 고장·잠김처럼 그 대여소 안에서 끝나는 일이면 제약을 만들지 않는다.
6. 바뀐 정류장과 싣기·내리기 대수를 계산 결과에서 인용해 짧게 알린다. 제약을 요청했으면 관리자 승인을 기다린다고 한 문장 덧붙인다.
