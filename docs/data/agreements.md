# 합의할 것 (6 데이터·시뮬레이터)

6이 만든 데이터와 코드가 다른 역할의 이름·형식과 맞아야 하는 곳을 한 번에 정하려고 모았다. 각 항목은 6의 **제안**이고, 정해지면 상태를 `합의`로 바꾸고 [data-spec.md](data-spec.md)와 `docs/specs/`에 반영한다.

**회의 진행:** 1~5번은 이름·형식이라 여러 역할이 같이 쓴다. 먼저 정한다. 나머지는 상대 역할과 따로 정해도 된다.

**2026-10-21 갱신:** PR #10 리뷰 댓글(1 에이전트, 3 MCP·RAG, 4 백엔드)을 상태 열에 반영했다. 12·13·23번은 제안 내용이 바뀌었다.

**2026-10-21 결정:** 1~4번과 12·13·15·25번을 @HuitaePark가 정했다 (상태 열의 **결정**). 대여소 ID는 제안과 달리 **실시간 API ID `ST-숫자`**로 정했다. 이유와 결과는 [ADR 0001](../adr/0001-station-id-is-realtime-api-id.md), 용어는 [CONTEXT.md](../../CONTEXT.md). 역할별로 바꿀 것은 맨 아래 [결정 뒤 할 일](#결정-뒤-할-일-2026-10-21).

---

## 모두 (이름·형식)

| # | 맞출 것 | 제안 | 다른 선택지 | 쓰이는 곳 | 상태 |
| --- | --- | --- | --- | --- | --- |
| 1 | 대여소 ID `stationId` | **대여소번호 문자열, 앞의 0 없음** (`"108"`). 실시간 API의 `ST-10`은 수집할 때 한 번만 바꾼다 | `"00108"`(대여이력 API의 5자리), `"ST-10"`(실시간 API) | `openapi` StationStatus·Stop·FieldReport, `internal-api`, `asyncapi` SolveStation, `ref_stations.csv`의 `station_id` | **결정** (2026-10-21): 제안과 달리 **실시간 API ID `ST-숫자`** ([ADR 0001](../adr/0001-station-id-is-realtime-api-id.md)). 대여소번호는 `stationNo` 속성으로 따로 둔다. 두 번호는 다른 체계라(번호 102 = `ST-4`, `ST-102` = 번호 505) 문자열로 바꾸지 않고 대응표로 바꾼다 |
| 2 | 구역 ID `regionId` | 활동 구역 17개를 **`N01`~`N08`(강북), `S01`~`S09`(강남)**. 화면에는 이름(`강남-05`)을 따로 보여 준다 | `gangnam-05`처럼 영문 이름, 한글 그대로(`강남-05`. URL·Kafka 키에 불편) | `openapi` `/api/regions/{regionId}`, `/api/kpi`, 근무조 ID, `ref_regions.csv` | **결정** (2026-10-21): 제안대로 `N01`~`S09`. 시연용 `gwangjin-seongdong`은 없앤다 (25번) |
| 3 | 근무조 ID와 시각 | `{regionId}:{yyyy-MM-dd}:{AM\|PM\|EVE}` (팀 명세 그대로). **AM 07:00–10:00, PM 13:00–16:00, EVE 19:00–21:00**, 계획 초안은 30분 전 (PRD 7.4) | EVE를 19:00–20:00로 (PRD 표기 "19–20시"를 끝 시각으로 읽기) | 근무조 ID, `SolveTruck.availableUntil`, 시뮬레이터 `SHIFTS` | **결정** (2026-10-21): 제안대로. EVE는 19:00–21:00 |
| 4 | 시각 표기 | 데이터 파일은 KST이고 시간대 표기가 없다. **API의 `date-time`은 `+09:00`을 붙인다** (`2026-10-21T07:00:00+09:00`) | UTC(`Z`)로 통일 | 모든 API·Kafka 메시지의 시각 필드 | **결정** (2026-10-21): 제안대로 `+09:00`. 받는 쪽은 RFC 3339라 어떤 오프셋이든 읽는다 |
| 29 | 예측 구간 `horizonH` | 최대 **5시간**, 기본 3시간(PRD FR-01 그대로). 선행 연구가 5시간까지 예측했다. 예측 시점 기준 모델의 구역 수요 오차율은 1시간 뒤 10.7%에서 5시간 뒤 15.4%로 완만하게 나빠지며, 5시간 뒤도 베이스라인(28.3%)보다 낫다 | 지금 명세의 최대 6시간 유지 | `openapi` `/api/regions/{regionId}/stations`, `internal-api` `/internal/stations/risk`·`/internal/forecasts`, `get_forecast` 도구 | **합의** (3·4), 반영됨: openapi 0.11.1·internal-api 0.6.1에서 최대 5. MCP 도구는 이미 1~5만 받는다 |
| 5 | 이름 바꾸는 규칙 | 데이터 파일·DB 열은 snake_case, API는 camelCase. 같은 뜻은 같은 단어를 쓴다 (`docks` ↔ 거치대 수, `elevation_m` ↔ `elevationM`, `model_version` ↔ `modelVersion`) | | 시드, 예측, 시뮬레이터 결과 | 4 찬성 |

## 백엔드 (4)

| # | 맞출 것 | 제안 | 쓰이는 곳 | 상태 |
| --- | --- | --- | --- | --- |
| 6 | `ref.stations` 시드 열 | `station_id`(text), `api_station_id`(text, `ST-xxx`), `name`, `region_id`, `district`, `address`, `lat`, `lon`, `docks`(int), `elevation_m`(numeric), `role`(`FIXED_PICKUP`·`FIXED_DROPOFF`·`VARIABLE`). 파일: `analysis/out/ref_stations.csv` (운영 중 2,716곳) | Flyway 시드, `StationStatus` | **합의** (4): Flyway로 표를 만들고 CSV를 적재한다 (M3). 1번 결정으로 backend 표는 `station_id` = CSV의 `api_station_id`(`ST-4`), `station_no` = CSV의 `station_id`(`102`)로 담는다. CSV 열 이름은 그대로 둔다 |
| 7 | `ref.regions` 시드 열 | `region_id`, `name`, `side`(`NORTH`·`SOUTH`), `stations`, `depot_lat`, `depot_lon`, `trucks_min`(바쁜 날 동시 트럭 하한). 파일: `analysis/out/ref_regions.csv` (17행) | 구역 목록, 트럭 기본값 | **합의** (4) |
| 8 | 시드 갱신 | 6이 주 1회 대여소 정보를 다시 받아 시드를 새로 낸다. backend는 `station_id`로 덮어쓰고(upsert), 새 시드에 없는 대여소는 지우지 않고 운영 종료로 표시한다(지난 계획이 참조하므로) | 시드 적재 방식 | **합의** (4): 운영 여부 열을 하나 둔다 |
| 9 | 역할 표시 | 고정 회수 = `FIXED_PICKUP`, 고정 배치 = `FIXED_DROPOFF`, 변동 = `VARIABLE`. 2025년 이용 기록이 없는 대여소(신설)는 `VARIABLE` | `StationRole` | **합의** (4) |
| 10 | 성과 지표에 실패 대여 추가 | `PolicyKpi.failedRentals`(기록된 대여만 재생, 효과의 하한)와 `failedRentalsAdjusted`(실제로 비어 있던 시간의 억눌린 수요까지 넣은 값, 위쪽 추정)를 더한다. 정책 판단은 실패 대여로 하고 두 값 사이로 보고한다 | `/api/kpi`, 성과 리포트 화면 | **합의** (4): `/api/kpi`를 만들 때(M5) 계약에 넣는다 |
| 11 | 시뮬레이터 결과를 넘기는 방법 | 6이 정책·구역·날짜별 결과를 `sim_kpi.csv`(`date`, `region_id`, `policy`, `failed_rentals`, `failed_rentals_adjusted`, `empty_station_minutes`, `overflow_station_minutes`, `bikes_moved_per_truck_hour`)로 내고 backend가 적재해 `/api/kpi`로 준다. 다른 선택지: DB 테이블을 6이 직접 쓰기 | `/api/kpi` | **합의** (4): CSV를 backend가 적재 (M4) |
| 12 | 실시간 재고 수집 | **바뀜:** 이관하지 않는다. 따릉이·기상청 수집은 6이 계속 맡고 backend는 수집하지 않는다(ROLES, PR #13). 6이 수집기를 서버 compose 서비스로 올려 업무 DB 표(`observed_at` timestamptz, `station_id`, `bikes`, `docks`)에 쓰고, backend는 읽기만 한다. 컨테이너·배포는 4·5가 돕는다 | `StationStatus.bikes`, `observedAt` | **결정** (2026-10-21): 4가 Flyway로 `live.station_status`(`observed_at`, `station_id`, `bikes`, `docks`)를 만들고 6은 5분마다 추가만 한다. `station_id`는 API가 준 `ST-` 값 그대로 |
| 13 | `/internal/forecasts`의 값 | **바뀜:** 6이 미리 계산해 DB 표에 둔다. 근무조 시작 30분 전에 1~5시간 값을 넣는다. backend가 Python 예측기를 부르지 않아도 되고, 같은 근무조에 같은 값이 나와 재현된다(NFR-04) | `get_forecast` 도구 | **결정** (2026-10-21): 4가 Flyway로 `forecast.station_forecasts`(`shift_id`, `station_id`, `start`, `horizon_h` 1~5, `rentals`, `returns`, `rentals_h`·`returns_h`, `model_version`)를 만들고 6이 근무조 시작 30분 전에 넣는다. 예측기는 대여소번호를 쓰므로 넣을 때 `ST-`로 바꾼다 |

## 최적화 (5)

| # | 맞출 것 | 제안 | 쓰이는 곳 | 상태 |
| --- | --- | --- | --- | --- |
| 14 | `predict()` 시그니처 | `predict(station, start, horizon_h) -> Forecast`. `Forecast`는 시간별 `rentals_h`·`returns_h`와 합계 `rentals`·`returns`, `model_version`. 가짜 예측기까지 `or-tools/`에 반영해 두었다 ([predict.md](predict.md)) | `compute_imbalances` | 5가 안 B의 인터페이스를 최신 코어에 맞춰 `hyunmoo`에 반영했다. 시간별 실수·모델 버전을 유지하고 최종 재고 하한 올림·상한 내림으로 필요량을 만든다. 기본 1시간·구간 끝 정책은 유지하며, 3시간 기본값·궤적은 16번 후속이다. 두 Forecast 클래스와 운영 메시지 형식은 유지한다 |
| 15 | 내부 API `Forecast`에 시간별 값 | `rentalsH`·`returnsH`(number 배열, 선택)를 추가한다. 합계만 있으면 3시간 중간에 비는 대여소를 놓친다 | `internal-api` Forecast (백엔드·MCP도 함께) | **결정** (2026-10-21): 13번 표의 `rentals_h`·`returns_h`를 `internal-api` Forecast의 `rentalsH`·`returnsH`로 준다 |
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
| 25 | 계획 단위 | 활동 구역 17개(전 구역). 개발 기준 구역은 S05(강남-05) ([regions.md](regions.md)) | **결정** (2026-10-21): 시연·개발 범위는 **서울 전체 17개 구역**. 개발 기준 구역(S05)이나 시연 구역(`gwangjin-seongdong`)을 따로 두지 않는다. 계획·근무조는 여전히 구역 하나 단위다. 큰 구역(N01 265곳)이 NFR-01의 200곳을 넘는 18번을 함께 풀어야 한다 |
| 26 | 주 지표 | PRD 9.2의 주 지표(빈 대여소-분)는 그대로 보고하되, 정책 판단은 실패 대여로 한다 | 6 결정, PRD 반영 필요 |
| 27 | 단순 규칙 정의 | 빈 위험(재고 2대 이하)은 채우고, 회수는 고정 회수 대여소에서만 한다 | 6 결정, PRD 9.1 반영 필요 |
| 28 | PRD·ROLES 반영 | [06-data-sim.md](../roles/06-data-sim.md) 7절의 목록 | 제안 |

## 결정 뒤 할 일 (2026-10-21)

위 결정으로 역할마다 바꿀 것이다. 시연 구역(`gwangjin-seongdong`, 평가용 `yeongdeungpo`)은 **가짜 데이터(2·3)를 실제 구역·대여소 ID로 옮긴 뒤에** 화면 목록에서 뺀다. business-mcp 가짜는 다른 구역·근무조를 거부하므로 순서를 바꾸면 지금 시연 흐름이 끊긴다.

**지금 가짜·더미 데이터의 대여소 ID는 실제로 있는 다른 대여소의 ID다.** 예: 시연 데이터의 `ST-102`는 실제로는 자양사거리 광진아크로텔 앞, `ST-502`·`ST-511`도 다른 실제 대여소다. 실제 대여소로 옮길 때 이름과 ID를 대응표(`ref_stations.csv`)에서 함께 가져온다.

| 역할 | 할 일 |
| --- | --- |
| 1 에이전트 | 에이전트가 내보내는 시각을 `+09:00`으로. 근무조 ID·구역 예시를 `N02:2026-10-21:AM` 모양으로 |
| 2 Harness | `id_pattern: ST-\d+`는 **그대로** (바꿀 것 없음). 평가셋·가짜 MCP(`agent/evaluation/fake_mcp.py`)의 구역 `yeongdeungpo`와 근무조 ID를 `N`·`S` 구역으로, 대여소 ID(`ST-2001` 등)를 실제 대여소로 |
| 3 MCP·RAG | `StationId`는 `^ST-\d+$`만 받기 (대여소번호 형식 빼기), `RegionId`는 가짜 데이터를 옮긴 뒤 `^[NS]\d{2}$`로. 가짜 데이터의 구역·대여소를 실제 ID로. [MCP.md](../MCP.md)의 미결 표(대여소·구역 ID 형식) 정리 |
| 4 백엔드 | 대여소·구역 기준 표 적재(`ref.stations`, `ref.regions`), 재고·예측 표(`live.station_status`, `forecast.station_forecasts`)와 Forecast 시간별 값, 시각 `+09:00`, 대여소 API, 경로 계산 입력(`solve.requests`의 `availableUntil`에 EVE 끝 21:00). 가짜 에이전트(`FakeAgent.java`)와 테스트의 `gwangjin-seongdong`·`ST-101`~`ST-104`를 실제 구역·대여소로 |
| 5 최적화 | 시드에는 이미 `api_station_id` 열이 있다. 메시지로 주고받는 대여소 ID를 `api_station_id`(`ST-`)로 쓰고, 시드를 17개 구역으로 넓힌다(시드 해시 테스트 `test_seeds.py` 함께). 큰 구역 성능(18번, N01 265곳) 확인 |
| 6 데이터 | 실시간 수집기는 API의 `ST-` 값을 그대로 `live.station_status`에. 예측은 `ST-`로 바꿔 `forecast.station_forecasts`에. 시뮬레이터 `SHIFTS`의 EVE 19–21, `sim_kpi.csv`의 `region_id`는 `N`·`S` 구역. 내보내는 시각은 `+09:00` |
| 화면 담당 | 더미·시연 데이터의 대여소 ID(`ST-5xx`, `ST-101`~`ST-104`)는 실제 다른 대여소를 가리킨다. 대여소 API(4)가 나오면 더미를 걷어 내고, 그 전까지 `더미` 표시 유지. 시연 구역 `gwangjin-seongdong`은 `shared/region/regions.ts`, `shared/dummy.ts`, `plan-map/demo.ts`, `TaskConsoleView.vue` 기본값, `tests/dashboard.test.ts`에 있다(가짜 데이터를 옮긴 뒤 뺀다). 시각은 문자열로 정렬하지 말고 시각으로 비교한다(`+09:00`과 `Z`가 섞이면 순서가 틀린다) |
