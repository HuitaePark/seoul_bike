# 데이터 명세

| 항목 | 내용 |
| --- | --- |
| 문서 상태 | 초안 v0.1 (M1 계약 회의에서 확정) |
| 작성일 | 2026-10-20 |
| 만드는 코드 | `analysis/collect/stations_api.py`, `analysis/stations.py`, `analysis/export_region.py`, `analysis/collect/bikelist.py` |

팀이 같이 쓰는 데이터의 열과 규칙이다. 열은 **추가만** 하고 이름을 바꾸거나 지우지 않는다 ([ARCHITECTURE](../ARCHITECTURE.md) 5.3).

---

## 1. 공통 규칙

| 규칙 | 내용 |
| --- | --- |
| 시각 | 모두 KST, 시간대 표기 없는 `TIMESTAMP` |
| 대여소 키 | 내부 기준은 **`st_no`** (정수, 대여이력의 대여소번호). 실시간 API는 **`st_id`** (`ST-xxx`)를 쓴다 |
| ID 대응 | `stations.csv`가 `st_no` ↔ `st_id`를 1:1로 묶는다 (3,246곳). 실시간 대여소 이름 앞 번호도 `st_no`와 같다 (`102. 망원역 1번출구 앞` → 102) |
| 운영 여부 | 대여소 정보 API에는 폐쇄·이전한 곳도 남아 있다. 지금 운영 중인 곳은 `active = true`인 2,716곳이다 (실시간 재고 기준). 과거 대여이력을 붙일 때는 전체를, 계획과 시드에는 운영 중인 곳만 쓴다 |
| 갱신 | 대여소 정보 API 원본을 `data/raw/stations/<날짜>/`에 받아 마스터를 다시 만든다 (주 1회). 2026-10-20 기준 실시간 2,716곳이 모두 마스터에 있다 |
| 출처 | 서울 열린데이터광장 `tbCycleStationInfo`·`bikeStationMaster`(대여소), `tbCycleRentData`(대여이력), `bikeList`(실시간 재고), 기상청 ASOS, 공공데이터포털 특일정보 |

---

## 2. 대여소 `stations.csv` (→ `ref.stations` 시드)

| 열 | 형식 | 설명 |
| --- | --- | --- |
| `st_no` | int | 대여소번호. 기준 키 |
| `st_id` | text | 실시간 API의 대여소 ID (`ST-xxx`) |
| `st_name` | text | 대여소 이름 |
| `district` | text | 자치구 |
| `addr` | text | 도로명 주소 |
| `region` | text | 강북 / 강남 (자치구로 한강 기준 구분) |
| `terr` | text | 활동 구역 (`강북-01` ~ `강남-09`). 서비스의 계획 단위 ([regions.md](regions.md)). 좌표가 없는 3곳만 빈 값 |
| `terr_src` | text | `rebal`: 역산 군집으로 정함 (2,805곳, 운영 중 2,695곳). `nearest`: 2025 재배치 기록이 없어 같은 강 쪽에서 가장 가까운 대여소의 구역을 붙임 (438곳, 운영 중 21곳) |
| `lat`, `lon` | float | WGS84 |
| `hold_num` | int | 거치대 수. 재고 상한이 아니라 참고값 (PRD 7.1) |
| `elev_m` | float | 고도 (m). OpenTopoData SRTM 30m라 절댓값은 부정확하고, 대여소 간 차이로만 쓴다. 새 대여소 고도는 `data/raw/stations/elev_opentopodata.csv`에 쌓는다 |
| `rel_elev` | float | 자치구 고도 중앙값과의 차이 (m) |
| `rentals_d`, `returns_d` | float | 2025-04~10 하루 평균 대여·반납 (대) |
| `ride_net_d` | float | `returns_d − rentals_d`. 양수면 쌓이는 곳 |
| `reb_in_d`, `reb_out_d` | float | 역산한 하루 평균 트럭 배치·회수 (대). 추정치 |
| `role` | text | 고정 회수 / 고정 배치 / 변동. 2025 이용이 없으면 빈 값 |
| `active` | bool | 가장 최근 수집일의 실시간 재고에 나온 대여소. 계획·시드 대상 |

**역할 정의 (2026-10-20 결정):** PRD 7.1 초기값 그대로 `|ride_net_d| ≥ 10`이면 고정 역할로 둔다. 209곳(고정 회수 95, 고정 배치 114)이다. 역산 정리의 "182곳"은 날마다 방향이 같은지 계산하려고 하루 대여 20건 이상, 150일 이상 이용한 대여소로 좁힌 분석용 숫자다. 역할 기준에는 쓰지 않는다.

---

## 3. 대여이력 `trips.parquet`

공유 묶음에는 권역 대여소가 출발이나 도착인 대여만 들어 있다. 완전 중복(같은 자전거, 대여일시, 대여소)은 지웠다.

| 열 | 형식 | 설명 |
| --- | --- | --- |
| `bike_id` | int | 자전거번호 숫자 부분 |
| `rent_dt`, `rtn_dt` | timestamp | 대여·반납 일시 |
| `rent_st`, `rtn_st` | int | 대여·반납 대여소 (`st_no`) |
| `use_min` | float | 이용 시간 (분) |
| `use_m` | float | 이용 거리 (m). 기록 오류가 있어 거리 분석에는 조심해서 쓴다 |

**주의:**

- 2025년 대여의 0.52%는 `rtn_st`가 비어 있다. 시뮬레이터에서는 대여와 반납을 모두 재생에서 뺀다 (자전거 수 보존).
- 2025년 대여이력의 대여소번호는 마스터와 100% (대여), 99.99% (반납) 맞는다.
- 2026년 파일에만 0~1분 같은 대여소 반납이 있다. 2024~2025 묶음에는 해당하지 않는다.

---

## 4. 실시간 재고 `data/realtime/bikelist/<날짜>/<시분>.csv.gz`

5분마다 스냅샷 하나. API가 준 값을 문자열 그대로 저장한다.

| 열 | 설명 |
| --- | --- |
| `ts` | 수집 시각 (KST, 분 단위) |
| `stationId` | `st_id` |
| `stationName` | `<st_no>. <이름>` |
| `rackTotCnt` | 거치대 수. 2026-10-20 기준 마스터 `hold_num`과 모두 같다 |
| `parkingBikeTotCnt` | **현재 재고 (대)** |
| `shared` | 거치율 (%) = 재고 ÷ 거치대 |
| `stationLatitude`, `stationLongitude` | 좌표 |

---

## 5. 날씨 `weather_hourly.parquet`

서울(108) ASOS 시간자료. 빠진 시간의 기온·습도·풍속은 앞 값으로, 강수는 0으로 채웠다.

| 열 | 단위 | 설명 |
| --- | --- | --- |
| `ts` | | 정시 |
| `temp` | ℃ | 기온 |
| `rain_mm` | mm | 그 시간 강수 |
| `humidity` | % | 습도 |
| `wind` | m/s | 풍속 |
| `rain_prev3h` | mm | 직전 3시간 강수 합 (젖은 노면) |

학습에는 관측값을 쓰지만 운영할 때는 예보만 있다. 예측 모델 평가 때 이 차이를 따로 확인한다.

---

## 6. 공휴일 `holidays.csv`

`date`, `name`. 공공데이터포털 특일정보.

---

## 7. 역산 트럭 방문 `rebal_visits.parquet`

대여이력에서 역산한 **추정치**다 ([역산 정리](https://claude.ai/artifact/9nxHaGQM8VVreyMqFTxoqb) 1절). 현행 운영 재현 정책의 입력으로 쓴다.

| 열 | 설명 |
| --- | --- |
| `st` | 대여소 (`st_no`) |
| `kind` | `pick` 회수, `drop` 배치 |
| `n` | 옮긴 대수 (위쪽 추정) |
| `lo`, `hi` | 방문 시각이 들어 있는 창. 실제 방문은 이 사이 어딘가 |
| `width_h` | 창 폭 (시간). 1시간 이하만 시각을 믿을 수 있다 |
| `n_partner` | 상대 대여소 수 (배치는 가져온 곳, 회수는 보낸 곳) |

---

## 8. 나중에 정할 것

| 대상 | 시점 |
| --- | --- |
| 시뮬레이터 결과 `sim.runs`, `sim.metrics` (4 성과 조회 API가 읽음) | M4 |
| 수집을 4의 스케줄러로 넘길 때 테이블 열 | M4 |
| 예측 결과 `telemetry.forecasts` | M2 ([predict.md](predict.md)) |
