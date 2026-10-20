---
version: 1
---
## 작업: 현장 보고 해석과 재경로
1. 보고에 적힌 대여소를 `business_get_station_detail`로 확인한다. 없는 대여소라는 오류가 오면 오류에 담긴 가까운 대여소 중에서 보고 내용과 맞는 것을 고른다.
2. 보고를 제약 조건으로 바꾼다.
   - 진입 불가·공사: `{"type": "blocked", "station_id": ...}`
   - 실제 재고가 앱과 다름: `{"type": "actual_bikes", "station_id": ..., "bikes": 보고된 값}`
3. `business_request_route_plan`에 제약 조건을 넘겨 남은 경로를 다시 계산한다.
4. 계산 결과의 경로를 그대로 `business_propose_route_change`로 제안한다. 경로를 직접 고치지 않는다.
5. 무엇이 바뀌었는지(빠진 정류장, 바뀐 대수)를 계산 결과를 인용해 설명한다.
