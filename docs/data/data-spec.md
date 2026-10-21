# 데이터 계약 (초안 v0.2)

6 데이터·시뮬레이터가 다른 역할과 주고받는 데이터와 인터페이스의 원본이다. 코드보다 이 파일을 먼저 고친다. API·Kafka 계약(`docs/specs/`)과 겹치는 이름은 그쪽을 따른다.

| 대상 | 내용 | 쓰는 쪽 → 받는 쪽 | 담당 |
| --- | --- | --- | --- |
| `analysis/out/stations.csv` | 대여소 마스터: 대여소번호, 위치, 거치대, 고도, 역할, 구역, 운영 여부 (1절) | 데이터 → backend 시드(`ref.stations`), optimizer, MCP | 데이터 |
| `DemandPredictor.predict()` ([predict.md](predict.md)) | 대여소별 시간별 예상 대여·반납 | forecast → optimizer, 내부 API `/internal/forecasts` | 데이터 + 최적화 |
| 실시간 재고 `data/realtime/bikelist/` | 5분 간격 대여소 재고 (3절) | 수집기 → 데이터(역산 검증). M4부터 수집기를 서버에 올려 업무 DB 표에 쓰고 backend는 읽기만 (합의 12) | 데이터 → 백엔드 |
| 시뮬레이터 결과 → `PolicyKpi` (`/api/kpi`) | 정책별 지표 (7절) | sim → backend → 성과 리포트 화면 | 데이터 + 백엔드 |
| `analysis/out/ref_stations.csv`, `ref_regions.csv` | `ref.stations`·`ref.regions` 시드. API 이름을 snake_case로 (agreements.md 6·7번) | 데이터 → backend | 데이터 + 백엔드 |
| [docs/evidence/rebal-summary.md](../evidence/rebal-summary.md) | 역산 분석 요약 (RAG 근거 문서) | 데이터 → rag-indexer | 데이터 + MCP |
| 공유 묶음 `data/shared/<구역>/` | 대여이력, 날씨, 공휴일, 역산 방문 (2, 4~6절). 깃 밖, 데이터 내부용 | 데이터 | 데이터 |

## 만드는 법

저장소 루트에서 실행한다. 환경은 `python3.12 -m venv .venv && .venv/bin/pip install -r requirements-data.txt`(최적화기의 `requirements.txt`를 포함한다). Python 3.12를 쓴다: 최적화기가 고정한 `ortools==9.14.6206`이 3.14용으로는 없고, CI도 3.12다.

```bash
/usr/bin/python3 analysis/collect/stations_api.py      # 대여소 정보 API 원본 (주 1회)
.venv/bin/python analysis/stations.py                  # 대여소 마스터
.venv/bin/python analysis/seed.py                      # ref 시드 (운영 중 대여소, 구역 17개)
.venv/bin/python analysis/validate_stock.py            # 역산 재고 검증 (stock-validation.md)
.venv/bin/python analysis/export_region.py 강남-05     # 공유 묶음
.venv/bin/python -m sim --terr 강남-05 --date 2025-06-11       # 정책별 지표
.venv/bin/python -m forecast --terr 강남-05 --month 2025-06    # 베이스라인 채점
.venv/bin/python -m forecast.lgbm --terr 강남-05               # LightGBM 학습·검증 (약 2분, 관측 날씨, 참고용)
.venv/bin/python analysis/weather_fcst.py                       # 기상청 과거 단기예보 정리 (data/shared/weather_fcst.parquet)
.venv/bin/python -m forecast.nowcast --terr 강남-05             # 예측 시점 기준 LightGBM, lead 1~5시간 (약 10분)
```

시험: `.venv/bin/python -m unittest sim.tests.test_engine forecast.tests.test_baseline forecast.tests.test_lgbm`, `(cd or-tools && ../.venv/bin/python -m unittest discover -s tests -t .)`, `(cd analysis/collect && /usr/bin/python3 -m unittest test_bikelist)`.

대여이력 원본과 역산 중간 결과는 6의 노트북에만 있다(대여이력 74GB, 깃헙 파일 한도 100MB). 다른 역할은 `stations.csv`만 받으면 된다.

## 바꿀 때

- 열과 필드는 추가만 한다. 지우거나 이름을 바꿔야 하면 받는 쪽 담당과 먼저 합의한다.
- 이 문서의 버전을 올리고 PR 설명에 무엇이 바뀌었는지 적는다.
- 계약 PR은 쓰는 쪽(데이터)과 받는 쪽 담당이 함께 승인한다.

## 이 초안이 정한 것

- 대여소는 **대여소번호**로 부른다. 파일에서는 `st_no`(정수), API의 `stationId`에서는 앞의 0을 뺀 번호 문자열(`"108"`)이다. 실시간 API의 `ST-xxx`는 수집할 때 한 번만 바꾼다.
- 시각은 모두 KST다.
- 계획 단위는 활동 구역 17개다 ([regions.md](regions.md)). 개발 기준 구역은 강남-05를 제안한다.
- 계획과 시드에는 운영 중인 대여소(`active`)만 쓴다. 과거 대여이력을 붙일 때는 전체를 쓴다.
- 역할은 PRD 7.1 그대로다(하루 평균 |반납 − 대여| ≥ 10). API `StationRole`과는 고정 회수 = `FIXED_PICKUP`, 고정 배치 = `FIXED_DROPOFF`, 변동 = `VARIABLE`로 대응한다.
- 예측은 시간별 값으로 준다. 합계는 API `Forecast`의 `rentals`·`returns`와 같고, `model_version`은 `modelVersion`이다.
- 시뮬레이터 정책 이름은 API `Policy` 값을 그대로 쓴다: `NONE`, `GREEDY`, `CURRENT_OPS`, `AGENT`.

## 확인이 필요한 것 (담당자에게)

이름·형식을 맞출 것은 [agreements.md](agreements.md)에 상대 역할별로 모았다. 대여소 ID(`stationId`), 구역 ID(`regionId`), 근무조 시각, 시각 표기, 시드 열, 예측 필드, 성과 지표가 들어 있다.

---

## 1. 대여소 `stations.csv`

대여소 정보 API(`tbCycleStationInfo`, 좌표 보충은 `bikeStationMaster`) 원본을 `data/raw/stations/<날짜>/`에 받아 만든다. 2026-10-20 기준 3,246곳이고, 운영 중인 2,716곳이 실시간 재고와 모두 맞는다(번호, 거치대 수 포함). 대여소 정보 API에는 폐쇄·이전한 곳도 남아 있어 `active`로 가린다.

| 열 | 형식 | 설명 |
| --- | --- | --- |
| `st_no` | int | 대여소번호. 기준 키. 실시간 대여소 이름 앞 번호와 같다 (`102. 망원역 1번출구 앞` → 102) |
| `st_id` | text | 실시간 API의 대여소 ID (`ST-xxx`) |
| `st_name` | text | 대여소 이름 |
| `district` | text | 자치구 |
| `addr` | text | 도로명 주소 |
| `region` | text | 강북 / 강남 (자치구로 한강 기준 구분) |
| `terr` | text | 활동 구역 (`강북-01` ~ `강남-09`). 좌표가 없는 3곳만 빈 값 |
| `terr_src` | text | `rebal`: 역산 군집으로 정함 (2,805곳, 운영 중 2,695곳). `nearest`: 2025 재배치 기록이 없어 같은 강 쪽에서 가장 가까운 대여소의 구역을 붙임 (438곳, 운영 중 21곳) |
| `lat`, `lon` | float | WGS84 |
| `hold_num` | int | 거치대 수 (API `docks`). 재고 상한이 아니라 참고값 (PRD 7.1) |
| `elev_m` | float | 고도 m (API `elevationM`). OpenTopoData SRTM 30m라 절댓값은 부정확하고 대여소 간 차이로만 쓴다. 새 대여소 고도는 `data/raw/stations/elev_opentopodata.csv`에 쌓는다 |
| `rel_elev` | float | 자치구 고도 중앙값과의 차이 (m) |
| `rentals_d`, `returns_d` | float | 2025-04~10 하루 평균 대여·반납 (대) |
| `ride_net_d` | float | `returns_d − rentals_d`. 양수면 쌓이는 곳 |
| `reb_in_d`, `reb_out_d` | float | 역산한 하루 평균 트럭 배치·회수 (대). 추정치 |
| `role` | text | 고정 회수 / 고정 배치 / 변동 (API `role`). 209곳이 고정(회수 95, 배치 114). 2025 이용이 없으면 빈 값 |
| `active` | bool | 가장 최근 수집일의 실시간 재고에 나온 대여소. 계획·시드 대상 |

역산 정리의 "182곳"은 날마다 방향이 같은지 계산하려고 하루 대여 20건 이상, 150일 이상 이용한 대여소로 좁힌 분석용 숫자다. 역할 기준에는 쓰지 않는다.

## 2. 대여이력 `trips.parquet`

공유 묶음에는 구역 대여소가 출발이나 도착인 대여만 들어 있다. 완전 중복(같은 자전거, 대여일시, 대여소)은 지웠다.

| 열 | 형식 | 설명 |
| --- | --- | --- |
| `bike_id` | int | 자전거번호 숫자 부분 |
| `rent_dt`, `rtn_dt` | timestamp | 대여·반납 일시 |
| `rent_st`, `rtn_st` | int | 대여·반납 대여소 (`st_no`) |
| `use_min` | float | 이용 시간 (분) |
| `use_m` | float | 이용 거리 (m). 기록 오류가 있어 거리 분석에는 조심해서 쓴다 |

- 2025년 대여의 0.52%는 `rtn_st`가 비어 있다. 시뮬레이터는 이런 대여를 대여·반납 모두 재생에서 뺀다 (자전거 수 보존).
- 2025년 대여이력의 대여소번호는 마스터와 100% (대여), 99.99% (반납) 맞는다.
- 2026년 파일에만 0~1분 같은 대여소 반납이 있다. 2024~2025 묶음에는 해당하지 않는다.

## 3. 실시간 재고 `data/realtime/bikelist/<날짜>/<시분>.csv.gz`

5분마다 스냅샷 하나(약 80KB). API가 준 값을 문자열 그대로 저장한다.

| 열 | 설명 |
| --- | --- |
| `ts` | 수집 시각 (KST, 분 단위) |
| `stationId` | `st_id` |
| `stationName` | `<st_no>. <이름>` |
| `rackTotCnt` | 거치대 수 |
| `parkingBikeTotCnt` | **현재 재고 (대)**. API `StationStatus.bikes` |
| `shared` | 거치율 (%) = 재고 ÷ 거치대 |
| `stationLatitude`, `stationLongitude` | 좌표 |

## 4. 날씨 `weather_hourly.parquet`

서울(108) ASOS 시간자료. 빠진 시간의 기온·습도·풍속은 앞 값으로, 강수는 0으로 채웠다. 운영할 때는 예보만 있으므로 예측 시점 기준 모델(`forecast/nowcast.py`)은 관측 대신 `data/shared/weather_fcst.parquet`(기상청 단기예보, 종로 격자 60·127, 발표 시각 `issue`·대상 시각 `target`·기온 `temp`·1시간 강수량 `pcp`·강수확률 `pop`)에서 예측 시점보다 1시간 이상 앞서 발표된 최신 예보를 쓴다. 2025-04~06 예보는 관측과 비교해 기온 오차 0.95℃, 비 옴·안 옴 일치 92.4%였다.

| 열 | 단위 | 설명 |
| --- | --- | --- |
| `ts` | | 정시 |
| `temp` | ℃ | 기온 |
| `rain_mm` | mm | 그 시간 강수 |
| `humidity` | % | 습도 |
| `wind` | m/s | 풍속 |
| `rain_prev3h` | mm | 직전 3시간 강수 합 (젖은 노면) |

## 5. 공휴일 `holidays.csv`

`date`, `name`. 공공데이터포털 특일정보. 베이스라인 예측기는 주말과 공휴일을 같은 요일 유형으로 본다.

## 6. 역산 트럭 방문 `rebal_visits.parquet`

대여이력에서 역산한 **추정치**다 ([역산 정리](https://claude.ai/artifact/9nxHaGQM8VVreyMqFTxoqb) 1절). 현행 운영 재현 정책(`CURRENT_OPS`)의 입력이다.

| 열 | 설명 |
| --- | --- |
| `st` | 대여소 (`st_no`) |
| `kind` | `pick` 회수, `drop` 배치 |
| `n` | 옮긴 대수 (위쪽 추정) |
| `lo`, `hi` | 방문 시각이 들어 있는 창. 실제 방문은 이 사이 어딘가 |
| `width_h` | 창 폭 (시간). 1시간 이하만 시각을 믿을 수 있다 |
| `n_partner` | 상대 대여소 수 (배치는 가져온 곳, 회수는 보낸 곳) |

## 7. 시뮬레이터 결과 → `PolicyKpi`

`sim.engine.replay()`가 구역 하나의 하루를 정책마다 재생해 낸다. `/api/kpi`의 `date`, `regionId`가 재생한 날짜와 구역이다.

| `Result` (sim/engine.py) | `PolicyKpi` | 설명 |
| --- | --- | --- |
| `empty_min` | `emptyStationMinutes` | 그날 운영한 대여소의 재고 0 시간 합. 주 지표 |
| `over_min` | `overflowStationMinutes` | 재고가 거치대 수를 넘은 시간 합 |
| `moved_per_truck_h` | `bikesMovedPerTruckHour` | 회수한 대수 ÷ 트럭 운행 시간 |
| `failed` (기록된 대여만 재생) | `failedRentals` (추가 제안) | 재고가 없어 실패한 대여 수. 효과의 하한 |
| `failed` (억눌린 수요를 더해 재생) | `failedRentalsAdjusted` (추가 제안) | 실제로 비어 있던 시간의 수요까지 넣은 값. 위쪽 추정 |

**판단 기준 (2026-10-20 결정):** 두 지표를 모두 보고하고, 정책의 좋고 나쁨은 실패 대여로 판단한다. 빈 대여소-분은 이용이 적은 대여소를 채워도 줄어든다. 처음 정의한 단순 규칙(넘치면 모두 회수)은 2025-06-11 강남-05에서 빈 대여소-분을 39,724에서 35,980으로 줄였지만 실패 대여는 469에서 572로 늘렸다. 아침 주거지와 낮 업무지구에서 곧 대여될 자전거를 빼 갔기 때문이다. 그래서 단순 규칙은 고정 회수 대여소에서만 회수한다.

| 정책 | 06-11 실패 (보정 전) | 06-11 실패 (보정 후) | 06-04 실패 (보정 전) | 06-04 실패 (보정 후) |
| --- | --- | --- | --- | --- |
| `NONE` | 469 | 1,800 | 390 | 1,492 |
| `GREEDY` | 411 | 1,762 | 411 | 1,451 |
| `CURRENT_OPS` | 130 | 1,518 | 46 | 1,151 |

**검증:** 현행 운영 재현으로 하루를 돌린 끝 재고는 다음 날 0시에 따로 복원한 재고와 거의 같다(06-11 합계 2,164 vs 2,158대, 대여소별 평균 절대 차 0.87대). 엔진과 재고 복원이 같은 기준으로 맞물린다.

**억눌린 수요 보정 (`sim/latent.py`):** 대여 기록은 실제로 자전거가 있던 곳에서만 생겼다. 그대로 재생(보정 전)하면 `CURRENT_OPS`는 구조상 실패가 적게 나오고, 실제로 비어 있던 시간을 채워 두는 정책은 점수를 받지 못한다. 그래서 실제로 재고가 0이던 시간에 빌리려던 사람을 만들어 더한다(보정 후). 대여율은 앞선 4주 같은 요일 유형·같은 시각의 대여 수를 자전거가 있던 시간으로 나눈 값이고, 목적지와 이용 시간은 그 대여소의 실제 대여에서 뽑는다. 06-11 강남-05에서는 1,402건(기록된 대여 시도의 약 8%)이 생겼다.

보정 전에는 현행 운영이 재배치 없음보다 실패를 72% 줄이는 것처럼 보이지만, 보정 후에는 16%다. 현행 운영도 실제로 비어 있던 시간의 수요를 대부분 놓치고 있었다. 본 시스템이 줄여야 할 대상이 이 억눌린 수요다.

빈 대여소에서 못 빌린 사람 일부는 근처 대여소에서 빌렸고 그 대여는 이미 기록에 있으므로, 보정 후 값은 억눌린 수요를 크게 잡는다. **정책 효과는 보정 전(하한)과 보정 후 사이로 보고한다.**

**본 시스템(`AGENT`, `sim/policies.py`):** 매시 그 시각의 실제 재고와 예측 시점 기준 예측기(`forecast/nowcast.py`)로 3시간 앞까지의 재고 궤적을 그린다. 궤적이 '2대 + 여유(예상 대여의 표준편차)' 아래로 내려가는 곳은 채우고, 공급처(전해 4~10월 역산 회수처)에서 거치대를 넘는 곳은 빼도 버틸 만큼만 뺀다. 경로는 5의 OR-Tools(`plan_routes`)다. 설정을 고른 과정과 결과(서울 17개 구역 1주일 포함)는 [sim-results.md](sim-results.md)에 있다.

결과를 어디에 저장해 backend가 읽을지(`sim.runs`, `sim.metrics` 등)는 M4에 백엔드와 정한다.
