# 서울 17개 구역 참조 시드 검증

역할 6의 기준 CSV `analysis/out/ref_regions.csv`(17구역), `ref_stations.csv`(2,716곳)를 직접 읽는다.
S05만 추출한 사본 대신 전 구역을 검증한다. 계획·근무조는 여전히 한 구역 단위다.
원본 커밋·SHA-256·행 수와 wire ID 열을 `source.json`에 기록한다. 원본 CSV는 이 작업에서 수정하지 않는다.

| 파일 | 내용 |
| --- | --- |
| analysis/out/ref_regions.csv | N01~N08·S01~S09의 이름·대여소 수·중심 좌표·트럭 하한 제안 |
| analysis/out/ref_stations.csv | 번호/API ID 대응, 좌표·거치대·역할 |
| infra/optimizer/seeds/source.json | 원본 커밋·해시·행 수, 대상 구역 목록 |

`depot_lat`·`depot_lon`은 대여소 좌표 평균이며 실제 차고지로 확정된 값이 아니다.
`trucks_min`도 추정 하한이다. runtime 설정에 이 값을 자동 적용하지 않는다.
구역·대여소 ID는 [팀 결정](../../../docs/adr/0001-station-id-is-realtime-api-id.md)을 따른다.
메시지는 `api_station_id`(`ST-`)를 쓰고, 데이터 예측기의 대여소번호는 대응표로 변환한다.
예를 들어 **200 → ST-45**, **102 → ST-4**다. 접두사를 붙이거나 떼서 변환하지 않는다.

저장소의 `or-tools/`에서 실행한다.

```bash
../.venv/bin/python -m rebalancing.seeds \
  --regions ../analysis/out/ref_regions.csv \
  --stations ../analysis/out/ref_stations.csv --all-regions

# 한 구역 조회도 전체 파일을 먼저 검증한다.
../.venv/bin/python -m rebalancing.seeds \
  --regions ../analysis/out/ref_regions.csv \
  --stations ../analysis/out/ref_stations.csv --region N01
```

`load_region_seeds(regions_path, stations_path)`는 구역 ID별 `RegionSeed`를 반환한다.
기존 `load_region_seed(..., region_id)`와 한 구역 JSON 형식도 유지한다.
중복·누락 열/행 너비·잘못된 좌표/역할/거치대·번호/API ID 형식·미등록 구역 참조·구역별 행 수를 검사한다.
다른 구역의 잘못된 행도 건너뛰지 않으며 구역을 가로지르는 ID 중복도 거부한다.

검증 결과는 `referenceOnly: true`다. 재고·필요량·우선순위·관측 시각·차량 적재량을 만들지 않는다.
실제 SolveRequest 입력은 역할 4·6이 준비한다. 운영 DB 시드 적재는 역할 4의 범위다.
데이터 갱신 시 두 원본 CSV와 `source.json`의 커밋·해시·행 수를 함께 갱신한다.
테스트는 17개 구역별 행 수, 전체 2,716개 ID의 일대일 대응과 원본 해시를 확인한다.

## N01 규모 측정

`--region`은 해당 구역 전체를 사용하며 `--stations`와 함께 쓸 수 없다.
원본에 현재 재고·수요가 없으므로 `--all-candidates`를 명시해야 한다.
실제 좌표·거치대·참조 중심점에 가상 작업량과 트럭 5대를 결합하는 규모 검사다.
입력 출처·ID 대응/원본/계획 해시를 결과에 기록한다. 운영 재경로의 성능 보장으로 사용하지 않는다.

```bash
../.venv/bin/python -m rebalancing.benchmark --region N01 --all-candidates \
  --trucks 5 --repeats 20 --scenario replan --cache-mode compare --profile-stages
```

[N01 측정 결과](../../../docs/optimizer/benchmarks/n01-265.md)에 PLAN·REPLAN 원시 표본과 한계를 기록한다.
