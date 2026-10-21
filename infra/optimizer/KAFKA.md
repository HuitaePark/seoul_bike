# Kafka 최적화 실행기 — 독립 개발 환경

계산 함수의 입출력은 공통 AsyncAPI 그대로다.
`solve.requests → 계산 → solve.results`와 요청 retry/DLQ를 실제 독립 브로커로 검증했다.
main에는 backend의 SolveGateway·SolveResultConsumer도 구현됐다. 실제 worker와의 운영 통합,
requestId별 runtime 자동 공급과 운영 Compose 연결은 아직 검증하지 않았다.

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

초기화 서비스는 개발용 `solve.requests`, `solve.results`, `solve.requests.retry`,
`solve.requests.dlq`를 각 1파티션·복제 1로 생성한다. 이미 있는 토픽은 다시 만들지 않는다.
worker는 requests와 requests.retry만 구독하고 토픽을 자동 생성하지 않는다.
운영에서는 역할 4가 requests.retry·requests.dlq도 기존 solve 토픽과 맞는 6파티션으로 준비하고
보존·접근 권한을 정한 뒤 worker를 시작해야 한다. 이 초기화 명령은 운영용이 아니다.
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
- eventId 재사용 시 내용이 달라지거나 requestId의 계산 입력이 바뀌면 해당 전달을 DLQ로 보낸다.
  새 eventId·occurredAt·traceId로 재전달된 같은 requestId는 최초 저장 결과를 사용한다.
- 계산 결과·결과 eventId·traceparent를 전용 로컬 파일에 원자적으로 저장하고 fsync한 뒤 발행한다.
  발행 ACK → 발행 완료 상태 저장 → 해당 입력 offset+1의 동기 commit 순서다.
- 결과 발행 실패는 저장된 결과를 유지한 채 제한 재시도한다. 재시작·재시도는 동일한 결과 eventId를 사용한다.
  원본 commit만 실패한 경우 재시작해도 완료 결과를 다시 발행하지 않는다.
- **발행 ACK 직후 상태 저장 전에 죽으면 같은 결과 eventId가 재전송될 수 있다.**
  물리적 exactly-once를 보장하지 않는다. backend 결과 소비자도 공통 명세대로 eventId 중복을 제거해야 한다.
- 계산 결과인 OK·FAILED·TIMEOUT은 모두 정상 결과 메시지로 저장·발행한다.
  같은 requestId로 계산을 다시 하지 않으므로 새 계산은 새 requestId를 사용한다.
- 상태 손상·기록 실패, 원본 offset commit 실패, Kafka 수신 오류나 retry/DLQ 전달 실패는 중단한다.
  해당 입력을 commit하지 않으며 이후 입력도 처리하지 않는다. 원인을 수정한 뒤 같은 상태·브로커·group-id로 재시작한다.

파일 상태는 업무 DB가 아니라 실행기 전용 중복 방지 기록이다. POSIX 파일 잠금으로 같은 디렉터리의
동시 실행을 막는다. **한 worker와 유지되는 한 상태 볼륨**이 전제다. 다른 디스크·별도 상태로의
재배치, 수평 확장, 볼륨 유실 복구는 보장하지 않는다. 자동 만료는 없으며 상태 크기·보존 정책도
운영 통합 시 정해야 한다. 이미 결과가 저장된 requestId는 설정 변경 후에도 기존 결과를 사용한다.

## 제한 재시도 · DLQ

기본 정책은 **최초 처리 + 추가 3회**, retry 레코드를 처리하기 전 **2초 대기**다.
`--retry-attempts`는 0..10(0은 바로 DLQ), `--retry-delay-seconds`는 유한수 0..60으로 조정한다.
재시도 대기는 순차 worker의 다른 요청 처리도 잠시 지연시킨다. SIGTERM/SIGINT로 대기를 중단하면
그 retry offset은 commit하지 않는다. 운영의 지연 예산은 backend 요청 대기 시간과 함께 조정해야 한다.

| 실패 | 동작 |
| --- | --- |
| 잘못된 JSON·UTF-8·봉투·shiftId 키, ID 내용 충돌 | 즉시 requests.dlq |
| requestId별 runtime 누락, 계산 함수의 예외, 결과 발행 실패 | requests.retry, 횟수 소진 시 requests.dlq |
| retry 횟수·원본 헤더 누락/중복/잘못된 값 | 즉시 requests.dlq. 재시도 횟수를 초기화하지 않음 |
| 저장소 손상·기록 실패, 원본 commit·retry/DLQ 발행 실패 | 미완료로 두고 worker 종료 |
| 정상적으로 반환된 OK·FAILED·TIMEOUT | 기존대로 solve.results. 자동 재계산하지 않음 |

retry/DLQ의 key·본문 bytes·eventId·traceId·traceparent는 유지한다. 본문이 깨졌거나 tombstone이면
그 원문을 보관하며 새 SolveResult를 만들지 않는다. 전달 ACK 후 원본 offset을 동기 commit한다.
DLQ로 간 요청은 결과 메시지가 없어 backend에서 시간 초과가 날 수 있으므로 운영자가 확인해야 한다.

| 헤더 | 값 |
| --- | --- |
| kafka_original-topic | 최초 solve.requests. retry 메타데이터 자체가 잘못되면 실제 수신 토픽 |
| kafka_exception-message | 예외 본문 대신 INVALID_ENVELOPE·INVALID_REQUEST·IDENTITY_CONFLICT·RUNTIME_CONTEXT_ERROR·COMPUTATION_ERROR·PUBLISH_FAILED·INVALID_RETRY_METADATA 중 고정 코드 |
| optimizer_original-partition·optimizer_original-offset | 최초 입력 위치. retry에서 보존하는 십진수 UTF-8 |
| optimizer_retry-attempt | retry에 발행할 때 1씩 증가. 최초 즉시 DLQ는 0, 소진 DLQ는 마지막 횟수 |
| optimizer_failure-stage | decode·validate·lookup·context·compute·publish·retry 중 실패 단계 |

이 헤더들은 worker의 전달 메타데이터이며 공통 SolveRequest 스키마를 확장하지 않는다.
원본 requests에 들어온 같은 이름의 헤더는 신뢰하지 않고 실제 입력 위치·횟수로 덮어쓴다.
다른 헤더는 그대로 보존한다. retry는 원본 토픽·파티션·offset·횟수의 유효성과 중복을 검사한다.

retry가 나중 요청보다 늦게 처리될 수 있으므로 토픽 사이의 전체 순서를 보장하지 않는다.
**전달 ACK 뒤 원본 commit 전에 죽으면 retry/DLQ 레코드도 중복될 수 있다.** 횟수는 각 전달 체인에서
유지되지만 이 장애로 생긴 복수 체인의 전역 총 시도 수까지 보장하지 않는다.
DLQ를 자동 재발행하지 않는다. 원인 수정·처리 결과 확인 후 별도 절차로 재처리해야 한다.
기존 파일 상태 버전·identity는 유지하므로 같은 상태 볼륨을 그대로 사용한다.

## 검증

worker의 생명주기와 요청 처리 로그는 JSON 한 줄씩 stderr에 남긴다.
라이브러리 로그와 명령 인자 오류는 다른 형식일 수 있다.

| 필드 | 의미 |
| --- | --- |
| event·loggedAt | worker.started/stopped, solve.started/completed/failed/routed와 UTC 기록 시각 |
| requestId·shiftId·traceId·eventId | 검증된 입력 상관관계. traceId는 입력에 있을 때만 기록 |
| resultEventId·resultTraceId | 실제 발행·재사용 결과의 ID. 중복 입력의 traceId와 다를 수 있음 |
| stage | decode·validate·lookup·context·compute·prepare·publish·mark_published·commit 중 현재 단계 |
| durationMs | 입력 처리 시작부터 저장·발행·offset commit까지. Kafka에서 기다린 시간은 제외 |
| computed·computeMs | 이번 전달에서 계산 함수를 호출했는지와 소요 시간. 저장 결과 재사용은 false·0 |
| resultSolveMs | 결과에 저장된 계산 시간. 재시작·중복 요청에서는 최초 계산의 과거 값 |
| outcome·status | published/duplicate와 계산 결과 OK/FAILED/TIMEOUT. completed는 전송 완료이며 계산 성공만 뜻하지 않음 |
| matrixCache | 캐시 공급자를 사용할 때 이번 요청의 hits·misses 증가량 |
| errorCode·errorType | 결과의 알려진 오류 코드 또는 실패 단계별 로그 전용 코드·예외 종류 |

solve.completed는 offset commit이 끝난 뒤 기록한다. 잘못된 봉투는 ID를 신뢰할 수 없으므로
solve.failed에 상관관계를 기록하지 않는다. 복구가 가능한 실패는 retry/DLQ ACK·원본 commit 후
solve.routed(outcome=retry/dead_letter, attempt, errorCode)를 기록한다. retry 메타데이터 자체의 오류는
상관관계와 attempt 없이 solve.routed에 INVALID_RETRY_METADATA를 기록한다. 복구 불가능한 오류는 종료한다.
요청 본문·좌표·정류장 목록·임의 헤더·예외 본문은 새 worker 로그에 기록하지 않는다.
오류 코드는 Kafka payload의 새 필드가 아니며, 위 정책이 실패 단계에 따라 재시도 여부를 결정한다.

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
일반 테스트 실행에서는 실제 브로커 테스트 2건을 건너뛰며, CI는 별도 단계에서 브로커를 띄워 실행한다.

SDK의 [발행 ACK·동기 commit API](https://docs.confluent.io/platform/current/clients/confluent-kafka-python/html/index.html)를 사용한다.
실제 worker와 backend 연결, 운영 retry/DLQ 토픽·관제, 실제 OSRM 도로 데이터,
전체 10초 목표와 서버 배포는 이번 검증 범위 밖이다.
