# 재배치 역산 스크립트

[따릉이 재배치 역산 정리](https://claude.ai/artifact/9nxHaGQM8VVreyMqFTxoqb)의 숫자를 만든 스크립트다. 공개 데이터에는 재배치 기록이 없어서, 자전거가 반납된 곳과 다음에 대여된 곳이 다르면 누군가 옮긴 것으로 보고 트럭 재배치를 거꾸로 추정한다.

따릉이 고장신고 프로젝트(개인 저장소 `dareungi`)에서 탐색용으로 만들어 2026-10-20에 옮겨 왔다. 스크립트 설명의 "본 프로젝트"는 `dareungi`를 뜻한다.

## 실행 환경

| 항목 | 내용 |
| --- | --- |
| 입력 | `dareungi`의 대여이력 Parquet(`data/processed/rent/`), 고장신고(`data/processed/fail_std.parquet`), 대여소(`data/interim/station_master.csv`, `station_cluster.csv`), `daily_master_v2`, 공휴일(`data/db/ops.duckdb`, 읽기 전용) |
| 입력 위치 | 환경 변수 `DAREUNGI` (기본 `/Users/junyeop/dareungi`) |
| 패키지 | duckdb, pandas, numpy. 09는 networkx·matplotlib, 한 곳은 scikit-learn(`BallTree`)도 쓴다. `dareungi`의 `.venv`에 모두 있다 |
| 출력 | `out/`. Parquet(최대 880MB)는 깃에서 빠지고 CSV·PNG만 깃에 있다 |

## 다시 만들기

```bash
cd analysis/rebal
python 01_gaps.py 2025 && python 01_gaps.py 2024     # 반납 → 다음 대여 구간
python 02_visits.py 2025 && python 02_visits.py 2024 # 트럭 방문으로 묶기
python 03_station_roles.py                           # 이 저장소의 대여소 마스터가 쓰는 결과
python 09_territory.py                               # 이 저장소의 활동 구역이 쓰는 결과
```

나머지 스크립트는 화면에 숫자를 찍는다. 역산 정리의 해당 절을 다시 확인할 때만 돌린다.

## 스크립트와 쓰인 곳

| 스크립트 | 하는 일 | 출력 | 역산 정리 | 이 저장소에서 쓰는 곳 |
| --- | --- | --- | --- | --- |
| `01_gaps.py` | 자전거별 반납 → 다음 대여 구간. 다른 대여소면 옮겨진 것 | `out/gaps_<기간>.parquet` | 1절 | 시뮬레이터 초기 재고 (`sim/engine.py` `load_day`) |
| `02_visits.py` | 옮겨진 자전거를 회수·배치 방문으로 묶기 | `out/visits_<연도>.parquet`, `bike_pick`·`bike_drop` | 1절, 4절(방문 시각의 원자료) | 시뮬레이터 초기 재고, 현행 운영 재현 (`export_region.py` → `rebal_visits.parquet`) |
| `03_station_roles.py` | 대여소별 이용 불균형과 재배치, 유형별 비교 | `out/station_roles.csv` | 2·3절 | `analysis/stations.py`의 이용·재배치 평균, 역할 |
| `04_stock.py` | 대여이력으로 재고 복원, 방문 직전 재고 | 화면 | 1절 표 | 시뮬레이터 초기 재고와 같은 방식 |
| `05_leisure.py` | 한강·공원 대여소, 불꽃축제 | 화면 | 5절 | |
| `06_region.py` | 강북·강남 두 권역으로 나눌 때 | 화면 | 6절 | |
| `07_closed_region.py` | 권역 안에서 자급할 수 있나 | 화면 | 6절 | |
| `08_loads.py` | 적재 1회 복원 시도 (시각 창이 넓어 쓰지 못함) | `out/loads_2025.parquet`, `load_edges_2025.parquet` | 7절 | |
| `09_territory.py` | 대여소 연결망 Louvain 군집으로 활동 구역 추정 | `out/territory.csv`, `territory_map*.png` | 7절 | `analysis/stations.py`의 구역(`terr`) |
| `10_split.py` | 한 곳에서 실은 자전거를 나눠 내리나 | 화면 | 3절 공급처 분산 | |
| `11_runs.py` | 운행 1회에 몇 곳을 들렀나 | 화면 | 7절 | |
| `12_weather.py` | 궂은 날에도 재배치하나 | 화면 | 9절 | |
| `13_realtime.py` | 트럭 도착 전 재고 감소, 회수 직후 빈 대여소 | 화면 | 10절 | |
| `14_events.py` | 행사장 주변에서 이용이 튄 날 찾기 | `out/event_days.csv` | 8절 | |
| `15_event_detail.py` | 상암 대형 행사 5건 | 화면 | 8절 | |
| `16_night_surge.py` | 공연 뒤 몰려 빌려 가는 밤 | `out/night_surge.csv` | 8절 | |
| `17_events_all.py` | 행사 유형별 비교 | `out/events_all.csv` | 8절 | |
| `18_weather_types.py` | 날씨 유형별 쌓이는 곳 | 화면 | 9절 | |
| `19_overpick.py` | 과다 회수 뒤 놓친 수요 | 화면 | 10절 | |

## 판단 기준값 (임시)

재배치 후보: 반납한 대여소와 다음 대여 대여소가 다르고, 그 사이 고장신고가 없고, 72시간 안에 다시 대여됐고, 두 대여소의 직선거리가 150m~10km. 기준을 바꾸면 수치도 달라지므로 바꿀 때는 민감도를 같이 남긴다.

## 확인한 것

2026-10-20에 경로를 환경 변수로 바꾼 뒤 `04_stock.py`를 다시 돌려 역산 정리 1절 표와 같은 값을 얻었다: 배치 직전 재고 중앙값 2대(30%가 0대), 회수 직전 31대(96%가 거치대 이상), 옮긴 대수 중앙값 13대.
