# Kafka 최적화 실행기 — 독립 개발 환경

계산 함수의 입출력은 공통 AsyncAPI 그대로다. 현재 개인 브랜치에서
`solve.requests → 계산 → solve.results`를 실제 로컬 브로커로 검증했다.
운영 backend의 요청 생성·결과 수신과 운영 Compose 연결은 아직 하지 않았다.

## 실행

Python 3.12 환경과 실행 중인 Docker 엔진이 필요하다. 저장소 루트에서 실행한다.

```bash
.venv/bin/python -m pip install -r requirements.txt -r or-tools/requirements-worker.txt -r or-tools/requirements-dev.txt
docker compose -f infra/optimizer/compose.kafka.yaml up -d --build --wait worker
```

uv로 만든 환경은 pip 대신 `uv pip install --python .venv/bin/python`에 같은 requirements를 전달한다.
브로커는 `127.0.0.1:19092`만 열며, 컨테이너는 `broker:9092`로 접속한다.
포트 충돌 시 `OPTIMIZER_KAFKA_PORT=19093`을 Compose 명령에 설정하고 호스트 접속 주소도 바꾼다.
기존 서버 Compose·네트워크·DB·토픽에는 연결하지 않는다.

초기화 서비스는 개발용 `solve.requests`, `solve.results`만 각 1파티션·복제 1로 생성한다.
이미 있는 토픽은 다시 만들지 않는다. 운영 토픽 구성이나 retry/DLQ 설정을 변경하지 않는다.
기본 worker는 아래 예시 요청에 대응하는 runtime 설정과 **개발용 하버사인 추정치**를 사용한다.

예시 메시지 발행:

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path
from confluent_kafka import Producer

payload = json.loads(Path("docs/specs/examples/optimizer/request.json").read_text())
producer = Producer({"bootstrap.servers": "127.0.0.1:19092", "acks": "all"})
errors = []
producer.produce("solve.requests", key=payload["shiftId"].encode(),
                 value=json.dumps(payload).encode(),
                 on_delivery=lambda error, message: errors.append(error))
assert producer.flush(10) == 0 and errors == [None], errors
PY
docker compose -f infra/optimizer/compose.kafka.yaml logs worker
docker compose -f infra/optimizer/compose.kafka.yaml exec broker rpk topic consume solve.results -n 1 -X brokers=broker:9092
```

같은 요청을 다시 보내도 완료 결과를 새로 발행하지 않는다. 결과 토픽을 처음부터 조회하면
첫 결과를 확인할 수 있다. 중지는 `docker compose -f infra/optimizer/compose.kafka.yaml down`이다.
`down --volumes`는 결과 중복 방지 상태까지 지우므로 평소 재시작에는 사용하지 않는다.

## 필수 외부 설정

[runtime.example.json](runtime.example.json)은 Kafka 스키마가 아니라 실행 설정 파일이다.

- requests의 키는 정확한 requestId다. 각 값에 shiftId·차고지 depot·고정 기준 시각 asOf가 필요하다.
- 누락·다른 shiftId·시간대 없는 시각은 거부한다. 메시지 생성 시각이나 소비 시각으로 대체하지 않는다.
- 여러 요청은 각각 항목을 추가한다. 파일은 시작할 때 읽으므로 갱신 후 worker를 재시작한다.
- 예시 좌표·시각은 가상 입력용이다. 실제 권역·스냅샷 값은 역할 4·6과 연결해야 한다.
- 공개 메시지에 임의 필드를 추가하지 않았다. 자동으로 설정을 공급하는 운영 resolver는 후속 작업이다.

호스트에서 worker만 실행하려면 저장소의 or-tools/에서 다음처럼 실행한다.

```bash
../.venv/bin/python -m rebalancing.worker \
  --bootstrap-servers 127.0.0.1:19092 --group-id optimizer-host-v1 \
  --contexts ../infra/optimizer/runtime.example.json \
  --state-dir ../.cache/optimizer-state --travel-source haversine
```

컨테이너 worker와 호스트 worker를 같은 브로커에 동시에 띄워 서로 다른 group-id로 처리하지 않는다.
테스트는 별도 개발 브로커에서 실행한다. 운영 도로 공급자는 `--travel-source osrm`,
`--osrm-url`, 명시적인 `--osrm-data-version`을 함께 전달한다.
디스크 이동 행렬 캐시는 `--matrix-cache-dir`로 선택한다.
실제 지도 준비는 [OSRM 안내](README.md)를 따른다.

## 중복·복구 규칙

- 입력 키가 payload.shiftId와 같은지 확인하고 traceId와 traceparent를 보존한다.
- eventId 재사용 시 내용이 달라지면 중단한다. requestId 재사용 시 계산 입력이 달라도 중단한다.
  새 eventId·occurredAt·traceId로 재전달된 같은 requestId는 최초 저장 결과를 사용한다.
- 계산 결과·결과 eventId·traceparent를 전용 로컬 파일에 원자적으로 저장하고 fsync한 뒤 발행한다.
  발행 ACK → 발행 완료 상태 저장 → 해당 입력 offset+1의 동기 commit 순서다.
- 발행 실패 시 commit하지 않는다. 재시작은 저장 결과와 동일한 결과 eventId를 사용한다.
  commit만 실패한 경우 완료 결과를 다시 발행하지 않는다.
- **발행 ACK 직후 상태 저장 전에 죽으면 같은 결과 eventId가 재전송될 수 있다.**
  물리적 exactly-once를 보장하지 않는다. backend 결과 소비자도 공통 명세대로 eventId 중복을 제거해야 한다.
- 계산 결과인 OK·FAILED·TIMEOUT은 모두 정상 결과 메시지로 저장·발행한다.
  같은 requestId로 계산을 다시 하지 않으므로 새 계산은 새 requestId를 사용한다.
- 잘못된 봉투·키, 설정 누락, ID 충돌, 상태 손상, 저장·송수신 실패는 미완료 입력을 commit하지 않고 중단한다.
  운영자는 원인을 수정하고 같은 상태 디렉터리·브로커 주소·group-id로 재시작한다.
- 자동 애플리케이션 재시도·retry/DLQ는 아직 없다. 횟수·간격·독성 메시지 처리 정책을 역할 4와 확정해야 한다.

파일 상태는 업무 DB가 아니라 실행기 전용 중복 방지 기록이다. POSIX 파일 잠금으로 같은 디렉터리의
동시 실행을 막는다. **한 worker와 유지되는 한 상태 볼륨**이 전제다. 다른 디스크·별도 상태로의
재배치, 수평 확장, 볼륨 유실 복구는 보장하지 않는다. 자동 만료는 없으며 상태 크기·보존 정책도
운영 통합 시 정해야 한다. 이미 결과가 저장된 requestId는 설정 변경 후에도 기존 결과를 사용한다.

## 검증

```bash
# 독립 브로커만 시작한다. 위의 worker가 실행 중이면 먼저 중지한다.
docker compose -f infra/optimizer/compose.kafka.yaml stop worker
docker compose -f infra/optimizer/compose.kafka.yaml up -d --wait broker
cd or-tools
OPTIMIZER_KAFKA_BOOTSTRAP=127.0.0.1:19092 ../.venv/bin/python -m pytest -q tests/integration/test_kafka_live.py
```

테스트는 자체 group-id·requestId·임시 상태를 사용하고 완료 후 소비자를 닫는다.
테스트용 요청이 토픽에 남으므로 이후 예시 worker를 시작할 때는 이전 테스트 상태와 섞지 않은
별도 Compose 프로젝트·브로커를 사용한다. 운영 브로커에서 이 테스트를 실행하지 않는다.
일반 테스트 실행에서는 실제 브로커 테스트 1건을 건너뛰며, CI는 별도 단계에서 브로커를 띄워 실행한다.

SDK의 [발행 ACK·동기 commit API](https://docs.confluent.io/platform/current/clients/confluent-kafka-python/html/index.html)를 사용한다.
백엔드 실제 연결, retry/DLQ, 실제 OSRM 도로 데이터, 전체 10초 목표와 서버 배포는 이번 검증 범위 밖이다.
