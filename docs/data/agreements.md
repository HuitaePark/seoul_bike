# 합의할 것 (6 데이터·시뮬레이터)

6이 만든 데이터와 코드가 다른 역할의 이름·형식과 맞아야 하는 곳을 한 번에 정하려고 모았다. 각 항목은 6의 **제안**이고, 정해지면 상태를 `합의`로 바꾸고 [data-spec.md](data-spec.md)와 `docs/specs/`에 반영한다.

**회의 진행:** 1~5번은 이름·형식이라 여러 역할이 같이 쓴다. 먼저 정한다. 나머지는 상대 역할과 따로 정해도 된다.

**2026-10-21 갱신:** PR #10 리뷰 댓글(1 에이전트, 3 MCP·RAG, 4 백엔드)을 상태 열에 반영했다. 12·13·23번은 제안 내용이 바뀌었다.

---

## 모두 (이름·형식)

| # | 맞출 것 | 제안 | 다른 선택지 | 쓰이는 곳 | 상태 |
| --- | --- | --- | --- | --- | --- |
| 1 | 대여소 ID `stationId` | **대여소번호 문자열, 앞의 0 없음** (`"108"`). 실시간 API의 `ST-10`은 수집할 때 한 번만 바꾼다 | `"00108"`(대여이력 API의 5자리), `"ST-10"`(실시간 API) | `openapi` StationStatus·Stop·FieldReport, `internal-api`, `asyncapi` SolveStation, `ref_stations.csv`의 `station_id` | 4 찬성. 3은 결정 전까지 `ST-1023`·`108` 둘 다 받는다. Harness `id_pattern`(`ST-\d+`), MCP 명세, 승인 카드 화면이 `ST-`를 가정해 1·2·3이 같이 고쳐야 하므로 **전원 합의 뒤 적용** |
| 2 | 구역 ID `regionId` | 활동 구역 17개를 **`N01`~`N08`(강북), `S01`~`S09`(강남)**. 화면에는 이름(`강남-05`)을 따로 보여 준다 | `gangnam-05`처럼 영문 이름, 한글 그대로(`강남-05`. URL·Kafka 키에 불편) | `openapi` `/api/regions/{regionId}`, `/api/kpi`, 근무조 ID, `ref_regions.csv` | 4 찬성(근무조 ID 예시를 `S05:2026-10-21:AM`으로). 3은 둘 다 받는다. 1번과 같이 **전원 합의 뒤 적용** |
| 3 | 근무조 ID와 시각 | `{regionId}:{yyyy-MM-dd}:{AM\|PM\|EVE}` (팀 명세 그대로). **AM 07:00–10:00, PM 13:00–16:00, EVE 19:00–21:00**, 계획 초안은 30분 전 (PRD 7.4) | EVE를 19:00–20:00로 (PRD 표기 "19–20시"를 끝 시각으로 읽기) | 근무조 ID, `SolveTruck.availableUntil`, 시뮬레이터 `SHIFTS` | 4 찬성. 1은 근무조 ID를 그대로 넘기므로 형식이 정해지면 따른다 |
| 4 | 시각 표기 | 데이터 파일은 KST이고 시간대 표기가 없다. **API의 `date-time`은 `+09:00`을 붙인다** (`2026-10-21T07:00:00+09:00`) | UTC(`Z`)로 통일 | 모든 API·Kafka 메시지의 시각 필드 | 4 찬성(backend는 지금 `Z`로 내보내므로 `+09:00`으로 바꾼다. 받는 쪽은 RFC 3339라 둘 다 읽는다). 1은 지금 UTC, 정해지면 2와 함께 맞춘다 |
| 29 | 예측 구간 `horizonH` | 최대 **5시간**, 기본 3시간(PRD FR-01 그대로). 선행 연구가 5시간까지 예측했다. 예측 시점 기준 모델의 구역 수요 오차율은 1시간 뒤 10.7%에서 5시간 뒤 15.4%로 완만하게 나빠지며, 5시간 뒤도 베이스라인(28.3%)보다 낫다 | 지금 명세의 최대 6시간 유지 | `openapi` `/api/regions/{regionId}/stations`, `internal-api` `/internal/stations/risk`·`/internal/forecasts`, `get_forecast` 도구 | **합의** (3·4), 반영됨: openapi 0.11.1·internal-api 0.6.1에서 최대 5. MCP 도구는 이미 1~5만 받는다 |
| 5 | 이름 바꾸는 규칙 | 데이터 파일·DB 열은 snake_case, API는 camelCase. 같은 뜻은 같은 단어를 쓴다 (`docks` ↔ 거치대 수, `elevation_m` ↔ `elevationM`, `model_version` ↔ `modelVersion`) | | 시드, 예측, 시뮬레이터 결과 | 4 찬성 |

## 백엔드 (4)

| # | 맞출 것 | 제안 | 쓰이는 곳 | 상태 |
| --- | --- | --- | --- | --- |
| 6 | `ref.stations` 시드 열 | `station_id`(text), `api_station_id`(text, `ST-xxx`), `name`, `region_id`, `district`, `address`, `lat`, `lon`, `docks`(int), `elevation_m`(numeric), `role`(`FIXED_PICKUP`·`FIXED_DROPOFF`·`VARIABLE`). 파일: `analysis/out/ref_stations.csv` (운영 중 2,716곳) | Flyway 시드, `StationStatus` | **합의** (4): Flyway로 표를 만들고 CSV를 적재한다 (M3) |
| 7 | `ref.regions` 시드 열 | `region_id`, `name`, `side`(`NORTH`·`SOUTH`), `stations`, `depot_lat`, `depot_lon`, `trucks_min`(바쁜 날 동시 트럭 하한). 파일: `analysis/out/ref_regions.csv` (17행) | 구역 목록, 트럭 기본값 | **합의** (4) |
| 8 | 시드 갱신 | 6이 주 1회 대여소 정보를 다시 받아 시드를 새로 낸다. backend는 `station_id`로 덮어쓰고(upsert), 새 시드에 없는 대여소는 지우지 않고 운영 종료로 표시한다(지난 계획이 참조하므로) | 시드 적재 방식 | **합의** (4): 운영 여부 열을 하나 둔다 |
| 9 | 역할 표시 | 고정 회수 = `FIXED_PICKUP`, 고정 배치 = `FIXED_DROPOFF`, 변동 = `VARIABLE`. 2025년 이용 기록이 없는 대여소(신설)는 `VARIABLE` | `StationRole` | **합의** (4) |
| 10 | 성과 지표에 실패 대여 추가 | `PolicyKpi.failedRentals`(기록된 대여만 재생, 효과의 하한)와 `failedRentalsAdjusted`(실제로 비어 있던 시간의 억눌린 수요까지 넣은 값, 위쪽 추정)를 더한다. 정책 판단은 실패 대여로 하고 두 값 사이로 보고한다 | `/api/kpi`, 성과 리포트 화면 | **합의** (4): `/api/kpi`를 만들 때(M5) 계약에 넣는다 |
| 11 | 시뮬레이터 결과를 넘기는 방법 | 6이 정책·구역·날짜별 결과를 `sim_kpi.csv`(`date`, `region_id`, `policy`, `failed_rentals`, `failed_rentals_adjusted`, `empty_station_minutes`, `overflow_station_minutes`, `bikes_moved_per_truck_hour`)로 내고 backend가 적재해 `/api/kpi`로 준다. 다른 선택지: DB 테이블을 6이 직접 쓰기 | `/api/kpi` | **합의** (4): CSV를 backend가 적재 (M4) |
| 12 | 실시간 재고 수집 | **바뀜:** 이관하지 않는다. 따릉이·기상청 수집은 6이 계속 맡고 backend는 수집하지 않는다(ROLES, PR #13). 6이 수집기를 서버 compose 서비스로 올려 업무 DB 표(`observed_at` timestamptz, `station_id`, `bikes`, `docks`)에 쓰고, backend는 읽기만 한다. 컨테이너·배포는 4·5가 돕는다 | `StationStatus.bikes`, `observedAt` | **합의** (4). 표 이름·위치는 같이 정할 것 (M4) |
| 13 | `/internal/forecasts`의 값 | **바뀜:** 6이 미리 계산해 DB 표에 둔다. 근무조 시작 30분 전에 1~5시간 값을 넣는다. backend가 Python 예측기를 부르지 않아도 되고, 같은 근무조에 같은 값이 나와 재현된다(NFR-04) | `get_forecast` 도구 | **합의** (4). 표 형식은 15번과 같이 정할 것 |

## 최적화 (5)

| # | 맞출 것 | 제안 | 쓰이는 곳 | 상태 |
| --- | --- | --- | --- | --- |
| 14 | `predict()` 시그니처 | `predict(station, start, horizon_h) -> Forecast`. `Forecast`는 시간별 `rentals_h`·`returns_h`와 합계 `rentals`·`returns`, `model_version`. 가짜 예측기까지 `or-tools/`에 반영해 두었다 ([predict.md](predict.md)) | `compute_imbalances` | 5 확인 필요. `or-tools/` 변경은 PR #10에서 빼서 `junyeop-ortools` 브랜치로 따로 제안했다(5의 `feat/optimizer-core`와 충돌 피하기). 6의 예측기는 같은 모양의 `Forecast`를 `forecast/`에 두고 쓴다 |
| 15 | 내부 API `Forecast`에 시간별 값 | `rentalsH`·`returnsH`(number 배열, 선택)를 추가한다. 합계만 있으면 3시간 중간에 비는 대여소를 놓친다 | `internal-api` Forecast (백엔드·MCP도 함께) | 4 찬성: 13번과 같이 계약에 넣는다 |
| 16 | 예측 구간과 궤적 검사 | `compute_imbalances`의 기본 예측 구간을 PRD대로 3시간으로 바꾸고, 구간 중간에 비는지 보는 검사를 넣는 시점을 정한다 (지금 기본 1시간) | `imbalance.py` | 질문 |
| 17 | `Station.capacity`의 뜻 | 지금 `Station.capacity`는 거치대 수, `Truck.capacity`는 적재 용량이라 이름이 같다. `Station.docks`로 바꾸기를 제안한다 | `models.py` | 제안 |
| 18 | 구역 규모 | 가장 큰 구역의 운영 대여소가 265곳(N01)이라 NFR-01 기준 200곳을 넘는다. 기준을 300곳으로 올리거나 큰 구역을 나눈다 | NFR-01·02, 성능 측정 | 질문 (기획 리드도) |
| 19 | 운행 파라미터 | 트럭 용량 15대, 정차 3분 + 대당 0.5분, OSRM 전에는 직선거리 × 1.3, 시속 20km. 시뮬레이터와 최적화가 같은 값을 쓴다 | `sim/engine.py`, `planner.py` | 제안 |
| 20 | 트럭 수 기본값 | 구역별 `trucks_min`(`ref_regions.csv`, 합계 35대). 하한이므로 정책 비교에서 트럭 수를 늘린 민감도를 같이 낸다 | 시뮬레이터, 계획 초안 | 제안 |

## MCP·RAG (3), 에이전트 (1), 평가 (2)

| # | 맞출 것 | 제안 | 쓰이는 곳 | 상태 |
| --- | --- | --- | --- | --- |
| 21 | 근거 문서 위치와 머리말 | `docs/evidence/*.md`. 맨 위 머리말에 `title`, `doc_id`, `version`, `updated`, `source`, `status`. 절 하나만 읽혀도 뜻이 통하게 쓴다. 첫 문서: [rebal-summary.md](../evidence/rebal-summary.md) | `rag-indexer`, `rag-mcp` Resource `doc://{doc_id}` | **합의** (3, PR #12). MCP가 더 쓰는 선택 필드 `doc_type`, `sources[]`, `data_period`는 그 뒤에 둔다. 작성 규칙: `docs/evidence/README.md` |
| 22 | 도구 결과의 대여소·구역 | 1·2번과 같은 ID를 쓰고, 이름은 따로 준다 | `business-mcp` 조회 도구 | **합의** (3): 백엔드 값을 그대로 넘기고 이름 필드(`name`, `stationName`)도 둔다 |
| 23 | 에이전트 스레드 | **바뀜:** `thread_id`는 작업 단위 `{shiftId}:{taskId}`다(asyncapi 0.3, PR #9). 근무조 ID를 그대로 쓰면 같은 근무조 작업끼리 대기 중인 승인이 섞인다(ISSUES ISS-03) | 에이전트 그래프 | **합의** (1·4), 이미 반영됨 |
| 24 | 평가셋의 현장 이벤트 | 시뮬레이터가 재고 불일치·진입 불가·트럭 지연을 주입한 시나리오를 2에게 넘긴다. 형식은 `FieldReportRequest`와 같게 | Harness 평가셋 | 제안 (M4) |

## 기획 리드

| # | 맞출 것 | 제안 | 상태 |
| --- | --- | --- | --- |
| 25 | 계획 단위 | 활동 구역 17개(전 구역). 개발 기준 구역은 S05(강남-05) ([regions.md](regions.md)) | 6 결정, 공유 필요 |
| 26 | 주 지표 | PRD 9.2의 주 지표(빈 대여소-분)는 그대로 보고하되, 정책 판단은 실패 대여로 한다 | 6 결정, PRD 반영 필요 |
| 27 | 단순 규칙 정의 | 빈 위험(재고 2대 이하)은 채우고, 회수는 고정 회수 대여소에서만 한다 | 6 결정, PRD 9.1 반영 필요 |
| 28 | PRD·ROLES 반영 | [06-data-sim.md](../roles/06-data-sim.md) 7절의 목록 | 제안 |
