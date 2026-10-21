# S05 참조 시드 검증

역할 6의 ad17528에서 S05(강남-05, 영등포·양천) 행만 추출한 개발용 스냅샷이다.
운영 DB 시드 적재나 공통 API 변경이 아니다.

| 파일 | 내용 |
| --- | --- |
| s05-region.csv | 권역 S05 1행: 이름·대여소 수·중심 좌표·트럭 하한 제안 |
| s05-stations.csv | 대여소 196곳의 번호/API ID 대응, 좌표·거치대·역할 |
| source.json | 원본 커밋·파일 해시, 추출 파일 해시와 행 수 |

**주의:** depot_lat·depot_lon은 실제 차고지가 아니라 대여소 좌표의 평균이다.
트럭 3대도 추정 하한이며 운행 확정값이 아니다. 개발 권역 S05와 ID·정책은 합의 전 제안 상태다.

저장소의 or-tools/에서 실행한다.

~~~bash
../.venv/bin/python -m rebalancing.seeds \
  --regions ../infra/optimizer/seeds/s05-region.csv \
  --stations ../infra/optimizer/seeds/s05-stations.csv \
  --region S05
~~~

JSON에는 ID 대응·좌표·거치대·역할, referenceDepot·trucksMin·좌표 경계가 나온다.
번호·API ID 중복, 누락 열·행 너비 오류, 잘못된 좌표·거치대·역할·거점 수를 거부한다.
station_id와 api_station_id는 대응표로 변환한다. 예를 들어 **200은 ST-45**이며,
ST-200으로 바꾸거나 접두사를 제거해서 매핑하지 않는다. 어느 ID를 wire에 쓸지는 이 모듈이 정하지 않는다.

재고 bikes·계산 필요량 neededBikes·우선순위·관측 시각·차량 적재량을 만들지 않는다.
실제 SolveRequest와 runtime 설정을 구성하려면 역할 4·6에서 해당 입력과 기준 시각을 받아야 한다.
OSRM 도로 파일·실제 차고지·운영 상한도 별도 확인 대상이다. 여기의 중심 좌표를 runtime에 자동 적용하지 않는다.

데이터 갱신 시 역할 6의 새 커밋에서 같은 권역을 다시 추출하고 source.json의 커밋·원본 해시·
추출 해시·행 수를 함께 갱신한다. CI는 추출 파일 해시와 실제 196행을 확인한다.
