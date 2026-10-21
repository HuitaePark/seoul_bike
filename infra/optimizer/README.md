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
  운영 Compose의 worker·상태 볼륨·기동 순서는 PR #46에 반영됐다. 실제 입력 조립은 PR #89에 반영됐다. 실데이터 적재·OSRM 운영 연결과 retry/DLQ 관제는 후속이다.
- 실제 N01 265곳·300곳 기준·5대의 도로 데이터 성능과 전체 재경로 10초 기준은 아직 측정하지 않았다.

공식 참고: [OSRM v26.10.0 HTTP API](https://github.com/Project-OSRM/osrm-backend/blob/v26.10.0/docs/http.md),
[전처리·CH 실행](https://github.com/Project-OSRM/osrm-backend/tree/v26.10.0),
[공식 컨테이너](https://github.com/Project-OSRM/osrm-backend/pkgs/container/osrm-backend).


## 운영 Compose에 선택적으로 연결

`compose.osrm.yaml`은 도로 자료와 서버 자원 확인 후 명시적으로 선택하는 추가 구성이다.
현재 `deploy.yml`과 기본 운영 Compose의 하버사인 worker는 유지한다. 자료가 없어 자동 전환하지 않는다.

1. 서울 17구역·차고지/연결도로가 포함된 PBF를 준비하고 출처·취득일·SHA-256·서버 RAM/디스크 예산을 기록한다.
2. 앞의 독립 구성에서 로컬 osrm을 중지하고 `osrm-prepare`로 CH 자료를 만든다. 운영 overlay 사용 중 자료를 바꿀 때에는
   같은 processed 폴더를 읽는 운영 osrm과 optimizer도 먼저 중지한다. data-version이 최종 완료 표시다.
3. 아래 결합 구성을 확인한 뒤, 승인된 로컬/서버 환경에서 해당 서비스를 기동한다. 기존 브로커/백엔드/상태 볼륨을 그대로 쓰므로
   별도 단독 worker를 같은 group-id/상태 디렉터리에 동시에 띄우지 않는다. 운영 테스트 입력과 완료 기준은
   [마무리 인계](../../docs/optimizer/final-handoff.md)를 따른다.

```bash
# 저장소 루트. 예시 환경파일로 구성만 검증한다.
docker compose --env-file infra/.env.example -f infra/compose.yaml \
  -f infra/optimizer/compose.osrm.yaml config -q

# 실제 실행 환경에서만. 서버의 env 파일은 ~/seoul-bike/.env이다. 이 작업에서는 실행하지 않았다.
docker compose -p seoul-bike --env-file ~/seoul-bike/.env -f infra/compose.yaml \
  -f infra/optimizer/compose.osrm.yaml up -d --build --wait osrm optimizer
```

서버에서 overlay를 켤 때 주의할 점:

- 서버의 env 파일은 `infra/.env`가 아니라 `~/seoul-bike/.env`이다. `deploy.yml`이 배포할 때마다 production 환경 값으로
  새로 쓰므로 이 파일을 손으로 고친 값은 다음 배포에서 사라진다. 바꿀 값은 production 환경 변수에 넣는다.
- `deploy.yml`은 `infra/compose.yaml`만으로 `up -d --remove-orphans`를 실행한다. 그래서 overlay로 OSRM을 켜 두어도
  다음 main 배포에서 osrm 컨테이너는 orphan으로 지워지고 optimizer는 하버사인 worker로 다시 만들어진다.
  배포에 overlay를 넣는 스위치(production 변수)가 생기기 전까지 서버의 OSRM 연결은 다음 배포 전까지만 유지된다고 본다.
  배포 뒤에는 optimizer가 어느 거리 계산을 쓰는지 다시 확인한다.
- overlay는 도로 자료를 `infra/optimizer/data/processed`(저장소 안 bind mount)에서 읽는다. 배포 runner의 작업 폴더는
  `actions/checkout`이 기본값(`clean: true`)으로 추적하지 않는 파일까지 지우므로, 그 안에 만든 전처리 결과도 배포 때 사라진다.
  서버에서는 runner 작업 폴더 밖에서 전처리하고 보관한다. 배포 스위치를 만들 때 이 경로도 함께 정한다.

추가 osrm은 호스트 포트를 열지 않는다. worker는 같은 네트워크의 `http://osrm:5000`과 읽기 전용
processed/data-version을 사용한다. 원래 `optimizer-state:/state`는 결과 중복 방지 때문에 보존하고,
행렬은 `optimizer-matrix-cache:/cache`에 별도로 둔다. 도로/엔진/프로필 변경 시 다시 전처리해 버전을 바꾼다.
같은 requestId는 도로 버전 변경 뒤에도 저장된 eventId·결과를 재사용한다. 새 계산은 새 requestId로 요청한다.
OSRM 실패를 하버사인으로 자동 대체하지 않는다. 기본 설정으로 되돌릴 때에도 상태 볼륨을 삭제하지 않는다.

이 PR에서는 Compose 결합·경로·볼륨·의존성 검증만 수행했다. 실제 PBF 전처리·컨테이너 구동·전체 성능은 미검증이다.
OSRM Table의 거리/시간을 연결하는 구성이며 지도 도로선 geometry를 공개 API에 추가하지 않는다.
