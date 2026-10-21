# 최적화 독립 개발 환경

역할 5가 도로 이동 행렬과 솔버를 단독 검증하기 위한 구성이다.
운영용 `infra/compose.yaml`과 별도 프로젝트·볼륨을 사용한다.
OSRM 실행 파일을 마련한 상태이며 **실제 도로 PBF 전처리·OSRM 컨테이너 검증은 아직 전**이다.
Kafka worker는 별도로 PR #46에서 운영 Compose에 편입됐다 ([운영 안내](../README.md#optimizer-경로-계산-worker)).

## 준비 및 실행

Docker 엔진이 실행되어 있어야 한다. 저장소 루트에서 실행한다.
데모 권역을 포함한 작은 OSM 도로 추출 파일을 직접 준비한다.
최적화 데모의 가상 대여소는 서울 여러 권역에 분포하므로, 그 좌표도 포함해야 한다.
대용량 전국 파일 다운로드·서버 데이터 변경은 이 구성에서 수행하지 않는다.

```bash
mkdir -p infra/optimizer/data/input infra/optimizer/data/processed
# 준비한 도로 파일을 infra/optimizer/data/input/region.osm.pbf 로 복사

# 도로 데이터 교체 전에는 이 로컬 OSRM을 먼저 중지한다.
docker compose -f infra/optimizer/compose.dev.yaml stop osrm
docker compose -f infra/optimizer/compose.dev.yaml --profile prepare run --rm osrm-prepare
docker compose -f infra/optimizer/compose.dev.yaml up -d --wait osrm
docker compose -f infra/optimizer/compose.dev.yaml --profile tools run --build --rm optimizer
```

전처리는 `osrm-extract` → `osrm-contract`의 CH 방식이다. 입력 PBF, car Lua 프로필,
엔진 버전·알고리즘의 해시를 `data/processed/data-version`에 저장한다.
같은 버전이면 전처리를 건너뛴다. 부분 실패 시 준비 완료 표시를 남기지 않는다.
전처리를 병렬로 실행하거나, OSRM이 데이터를 읽는 동안 파일을 교체하지 않는다.
임시 디렉터리에서 계산을 마친 뒤 결과를 교체하며, 원본 입력 파일은 읽기 전용이다.

OSRM 이미지는 `v26.10.0-debian`의 digest로 고정했다. 공식 레지스트리에서
Linux AMD64·ARM64 manifest를 확인했다. 버전을 바꿀 때는 Compose 이미지와
`prepare-osrm.sh`의 엔진 버전 문자열을 함께 갱신하고 다시 전처리한다.

호스트 포트는 기본 `127.0.0.1:5000`이다. 충돌하면 `OSRM_PORT=5001`을 지정한다.
컨테이너 사이에서는 계속 `http://osrm:5000`을 사용한다.
헬스체크는 TCP 포트 준비만 확인한다. 실제 도로의 연결성과 응답 형식은
마지막 optimizer 명령 또는 아래의 호스트 실행으로 확인한다.

## 호스트 Python에서 사용

[Python 환경 준비](../../docs/optimizer/README.md#실행) 후 `or-tools/`에서 실행한다.

```bash
../.venv/bin/python -m rebalancing --osrm-url http://127.0.0.1:5000 \
  --osrm-data-version "$(cat ../infra/optimizer/data/processed/data-version)" \
  --matrix-cache-dir ../.cache/travel-matrices --json
```

동일 실행을 반복하면 두 정책의 같은 좌표 행렬을 재사용한다.
JSON의 `cache_hits`, `cache_misses`는 그 프로세스에서의 조회 횟수다.
Docker 실행은 별도 `matrix-cache` 볼륨을 사용하므로 호스트 캐시와 공유하지 않는다.

## 범위와 후속 검증

- car 프로필의 정적 도로 시간이다. 실시간 교통·차량 높이·중량 등 트럭 전용 제약을 보장하지 않는다.
- 거리 값은 OSRM의 **가장 빠른 도로 경로의 거리**다. 두 점의 최단 거리와 다를 수 있다.
- 도로 스냅 탐색 반경은 기본 1km다. 권역 데이터에 맞춰 `--osrm-snap-radius-metres`로 조정한다.
- 도로망 단절, 지도 밖 좌표, HTTP 실패는 오류로 종료한다. 추정치로 자동 대체하지 않는다.
- 원본·전처리 파일과 캐시는 Git에서 제외한다. 지도 출처·라이선스·취득일은 권역 확정 후 별도 기록한다.
- Kafka 실행기와 개발용 solve 토픽 초기화는 [독립 Kafka 환경](KAFKA.md)에 있다.
  운영 Compose의 worker·상태 볼륨·기동 순서는 PR #46에 반영됐다. 실제 수요 입력·OSRM 운영 연결과 retry/DLQ 관제는 후속이다.
- 실제 200곳·5대의 도로 데이터 성능과 전체 재경로 10초 기준은 아직 측정하지 않았다.

공식 참고: [OSRM v26.10.0 HTTP API](https://github.com/Project-OSRM/osrm-backend/blob/v26.10.0/docs/http.md),
[전처리·CH 실행](https://github.com/Project-OSRM/osrm-backend/tree/v26.10.0),
[공식 컨테이너](https://github.com/Project-OSRM/osrm-backend/pkgs/container/osrm-backend).
