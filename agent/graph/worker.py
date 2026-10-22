# Author: Jung Minkyu
# Date: 20OCT2026
# Purpose: ai.tasks 소비자. 중복 거르기, 처리 중 예외의 재시도·DLQ, 진행 이벤트를 ai.events로 순서대로 발행한다.

"""Kafka 일꾼 (docs/AGENT-GRAPH.md 5절, asyncapi 공통 규칙).

토픽
- 소비: `ai.tasks`, `ai.tasks.retry` (backend가 FAILED 뒤 다시 넣는 것, 이 일꾼이 예외 뒤 다시 넣는 것)
- 발행: `ai.events` (키 shiftId), `ai.tasks.retry` (헤더 attempt), `ai.tasks.dlq` (헤더 reason)

메시지 1건
1. JSON·계약 검사. 틀리면 다시 해도 같으므로 바로 DLQ. 작업을 알 수 있으면(taskId·shiftId) `FAILED`도 보낸다.
   안 보내면 backend의 작업이 QUEUED에 멈춘다 (예: reportId 없는 FIELD_REPORT).
2. `eventId`를 이미 처리했으면 버린다 (Kafka는 '적어도 한 번' 전달).
3. 근무조 그래프로 처리하고, 진행 이벤트를 다 보낸 뒤 처리 기록을 남기고 오프셋을 커밋한다.
   이벤트 발행이 실패하면 버리지 않고 될 때까지 다시 보낸다. 그동안 커밋하지 않는다 (커밋했다면 이벤트는 나갔다).
4. 처리 중 예외(DB·브로커 장애 등)는 `ai.tasks.retry`로 다시 넣고, 3번째도 실패하면 DLQ와 `FAILED`.
   작업 실패(FAILED 이벤트)는 이 일꾼이 재시도하지 않는다. 재시도 여부는 backend가 정한다 (HARNESS 1.5).
   그래서 FAILED로 끝난 명령은 처리 기록을 남기지 않는다: backend가 같은 eventId로 다시 넣어도 처리된다.

추론 기록 `ai.traces` (관리자 로그, harness/trace.py)는 진행 이벤트와 일부러 다르게 다룬다 (`TracePump`).
따로 연 producer로 보내고, 대기열이 차거나 발행이 실패하면 버린다. 커밋 전에 기다리지 않는다.
그래서 이 통로가 막혀도 작업 처리·`ai.events` 순서·오프셋 커밋은 그대로다.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Protocol

from harness import ProgressEvent

from .contract import CommandError, TaskCommand, plain_hyphens
from .shift import ShiftAgent

log = logging.getLogger(__name__)

TASKS, RETRY, DLQ, EVENTS, TRACES = "ai.tasks", "ai.tasks.retry", "ai.tasks.dlq", "ai.events", "ai.traces"
READY_EVERY = 10  # 초. 준비 파일(compose healthcheck가 본다)을 이 간격으로 갱신한다

Send = Callable[[str, str | None, bytes, dict[str, str]], Awaitable[None]]
Sleep = Callable[[float], Awaitable[None]]


@dataclass
class Message:
    """Kafka 레코드에서 필요한 것만."""

    topic: str
    key: str | None
    value: bytes
    headers: dict[str, str] = field(default_factory=dict)


class Processed(Protocol):
    async def seen(self, event_id: str) -> bool: ...

    async def mark(self, event_id: str, task_id: str, status: str) -> None: ...


class MemoryProcessed:
    """테스트·개발용 처리 기록."""

    def __init__(self) -> None:
        self.done: dict[str, str] = {}

    async def seen(self, event_id: str) -> bool:
        return event_id in self.done

    async def mark(self, event_id: str, task_id: str, status: str) -> None:
        self.done.setdefault(event_id, status)


class PostgresProcessed:
    """운영용 처리 기록. 체크포인트 테이블과 따로 둔다."""

    DDL = """
    CREATE TABLE IF NOT EXISTS agent_processed_events (
        event_id     text PRIMARY KEY,
        task_id      text NOT NULL,
        status       text NOT NULL,
        processed_at timestamptz NOT NULL DEFAULT now()
    )"""

    def __init__(self, conn) -> None:
        self.conn = conn  # psycopg.AsyncConnection (autocommit)

    @classmethod
    @asynccontextmanager
    async def connect(cls, conn_string: str) -> AsyncIterator[PostgresProcessed]:
        import psycopg  # 운영에서만 필요한 의존성

        async with await psycopg.AsyncConnection.connect(conn_string, autocommit=True) as conn:
            await conn.execute(cls.DDL)
            yield cls(conn)

    async def seen(self, event_id: str) -> bool:
        cur = await self.conn.execute("SELECT 1 FROM agent_processed_events WHERE event_id = %s", (event_id,))
        return await cur.fetchone() is not None

    async def mark(self, event_id: str, task_id: str, status: str) -> None:
        await self.conn.execute(
            "INSERT INTO agent_processed_events (event_id, task_id, status) VALUES (%s, %s, %s) "
            "ON CONFLICT (event_id) DO NOTHING",
            (event_id, task_id, status),
        )


class EventPump:
    """그래프의 동기 publish → 비동기 발행. 한 줄 대기열이라 같은 작업의 이벤트가 낸 순서대로 나간다.

    backend는 받은 순서대로 seq를 매기므로(키 = shiftId, 같은 파티션) 발행 순서가 곧 화면 순서다.
    발행이 실패하면 그 이벤트를 될 때까지 다시 보낸다 (간격은 최대 `max_delay`초). 하나를 버리면 그 작업이
    backend에서 멈추고, 뒤 이벤트를 먼저 보내면 순서가 바뀐다. 브로커가 죽었으면 어차피 명령도 못 받는다.
    """

    def __init__(self, send: Send, *, sleep: Sleep = asyncio.sleep, max_delay: float = 10.0) -> None:
        self.send = send
        self.sleep = sleep
        self.max_delay = max_delay
        self.queue: asyncio.Queue[dict] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def publish(self, event: dict) -> None:
        self.queue.put_nowait(event)

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            event = await self.queue.get()
            try:
                await self._deliver(event)
            finally:
                self.queue.task_done()

    async def _deliver(self, event: dict) -> None:
        headers = {"type": event["type"], "taskId": event["taskId"]}
        value = json.dumps(event, ensure_ascii=False).encode()
        delay = 0.5
        while True:
            try:
                await self.send(EVENTS, event["shiftId"], value, headers)
                return
            except Exception:  # noqa: BLE001 - 버리지 않고 다시 보낸다
                log.exception("ai.events 발행 실패, %.1f초 뒤 다시: %s", delay, event.get("eventId"))
                await self.sleep(delay)
                delay = min(delay * 2, self.max_delay)

    async def flush(self) -> None:
        """대기열이 빌 때까지 기다린다 (커밋 전에)."""
        await self.queue.join()

    async def stop(self) -> None:
        await self.flush()
        if self._task:
            self._task.cancel()


class TracePump:
    """추론 기록 → `ai.traces`. 최선 노력이다: 진행 이벤트(EventPump)와 달리 다시 보내지 않고 버린다.

    `publish`는 막지 않는다 (대기열이 차면 버린다). 발행은 `timeout`초 안에 끝나지 않으면 버린다.
    버린 수는 `dropped`에 세고, 로그는 처음 한 번과 그 뒤 100건마다 남긴다 (브로커 장애 때 로그가 넘치지 않게).
    """

    def __init__(self, send: Send, *, max_queue: int = 2000, timeout: float = 5.0) -> None:
        self.send = send
        self.timeout = timeout
        self.queue: asyncio.Queue[dict] = asyncio.Queue(max_queue)
        self.dropped = 0
        self._task: asyncio.Task | None = None

    def publish(self, event: dict) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self._drop("대기열이 찼다")

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            event = await self.queue.get()
            try:
                value = json.dumps(event, ensure_ascii=False).encode()
                headers = {"kind": str(event.get("kind")), "taskId": str(event.get("taskId"))}
                await asyncio.wait_for(self.send(TRACES, event.get("shiftId"), value, headers), self.timeout)
            except Exception as exc:  # noqa: BLE001 - 관리자 로그가 작업을 막지 않게 버린다
                self._drop(f"{type(exc).__name__}: {exc}")
            finally:
                self.queue.task_done()

    def _drop(self, why: str) -> None:
        self.dropped += 1
        if self.dropped == 1 or self.dropped % 100 == 0:
            log.warning("ai.traces 추론 기록을 버렸다 (누적 %d건): %s", self.dropped, why)

    async def stop(self, timeout: float = 3.0) -> None:
        """남은 기록을 잠깐 보내 보고 멈춘다. 종료를 오래 붙잡지 않는다."""
        try:
            await asyncio.wait_for(self.queue.join(), timeout)
        except TimeoutError:
            log.warning("ai.traces 추론 기록 %d건을 보내지 못하고 멈춘다", self.queue.qsize())
        if self._task:
            self._task.cancel()


class TaskWorker:
    def __init__(self, shift: ShiftAgent, send: Send, processed: Processed, pump: EventPump, *,
                 max_attempts: int = 3) -> None:
        self.shift, self.send, self.processed, self.pump = shift, send, processed, pump
        self.max_attempts = max_attempts

    async def handle(self, msg: Message) -> str:
        """메시지 1건 처리. 반환: done / failed / ignored / duplicate / retry / dlq / invalid."""
        attempt = int(msg.headers.get("attempt", "0") or 0)
        raw = None
        try:
            raw = json.loads(msg.value)
            cmd = TaskCommand.parse(raw)
        except (ValueError, CommandError) as exc:  # JSONDecodeError도 ValueError
            await self._dlq(msg, attempt, f"계약 위반: {exc}")
            await self._fail_task(raw, f"계약에 맞지 않는 명령이라 처리하지 않았다 ({exc})")
            return "invalid"
        if await self.processed.seen(cmd.event_id):
            return "duplicate"
        try:
            outcome = await self.shift.handle(raw)
            await self.pump.flush()
        except Exception as exc:  # noqa: BLE001 - 처리 중 예외는 재시도 토픽으로
            log.exception("명령 처리 실패 %s (attempt %s)", cmd.event_id, attempt)
            reason = f"{type(exc).__name__}: {exc}"[:300]
            if attempt + 1 < self.max_attempts:
                await self.send(RETRY, msg.key, msg.value, {**msg.headers, "attempt": str(attempt + 1), "reason": reason})
                return "retry"
            await self._dlq(msg, attempt, reason)
            await self._fail_task(raw, f"처리 중 오류가 {self.max_attempts}번 이어져 멈췄다 ({reason})")
            return "dlq"
        status = outcome.get("status", "")
        if status == "FAILED":
            return "failed"
        await self.processed.mark(cmd.event_id, cmd.task_id, status)
        return "ignored" if status == "IGNORED" else "done"

    async def _fail_task(self, raw: object, why: str) -> None:
        """DLQ로 보낸 명령이라도 어느 작업인지 알면 FAILED(INTERNAL_ERROR)로 끝낸다. 화면이 '대기'·'진행 중'에서 멈추지 않게."""
        if not isinstance(raw, dict) or not all(isinstance(raw.get(k), str) and raw[k] for k in ("taskId", "shiftId")):
            return
        event = ProgressEvent("FAILED", raw["taskId"], str(raw.get("traceId") or raw["taskId"]), raw["shiftId"],
                              {"error": f"INTERNAL_ERROR: {why}", "code": "INTERNAL_ERROR"},
                              {"code": "INTERNAL_ERROR"})
        self.pump.publish(plain_hyphens(event.to_json()))
        await self.pump.flush()

    async def _dlq(self, msg: Message, attempt: int, reason: str) -> None:
        await self.send(DLQ, msg.key, msg.value,
                        {**msg.headers, "attempt": str(attempt), "reason": reason[:500], "sourceTopic": msg.topic})


async def _keep_ready(path: Path) -> None:
    """준비 파일을 계속 갱신한다. compose healthcheck는 이 파일이 최근에 바뀌었는지 본다."""
    while True:
        path.touch()
        await asyncio.sleep(READY_EVERY)


async def run_kafka(make_shift: Callable[[Callable[[dict], None], Callable[[dict], None] | None], ShiftAgent],
                    processed: Processed, bootstrap: str, *, group_id: str = "agent", stop: asyncio.Event | None = None,
                    ready_file: str | None = None, trace: bool = True) -> None:
    """Kafka에 붙어 명령을 계속 처리한다. 한 건을 끝까지 처리한 뒤에만 오프셋을 커밋한다.

    make_shift(publish, trace_publish) → ShiftAgent. trace_publish는 `ai.traces` 발행기(trace=False면 None)다.
    ready_file은 브로커에 붙은 뒤에 만들고 끝날 때 지운다 (healthcheck용).
    """
    from aiokafka import AIOKafkaConsumer, AIOKafkaProducer  # 운영에서만 필요한 의존성

    consumer = AIOKafkaConsumer(TASKS, RETRY, bootstrap_servers=bootstrap, group_id=group_id,
                                enable_auto_commit=False, auto_offset_reset="earliest")
    producer = AIOKafkaProducer(bootstrap_servers=bootstrap, enable_idempotence=True)
    # 추론 기록은 따로 연 producer로 보낸다. 기록이 몰려도 ai.events 발행이 같은 버퍼에서 기다리지 않는다
    trace_producer = AIOKafkaProducer(bootstrap_servers=bootstrap, acks=1, linger_ms=50) if trace else None
    await consumer.start()
    await producer.start()
    if trace_producer:
        await trace_producer.start()

    def sender(target):
        async def send(topic: str, key: str | None, value: bytes, headers: dict[str, str]) -> None:
            await target.send_and_wait(topic, value, key=key.encode() if key else None,
                                       headers=[(k, str(v).encode()) for k, v in headers.items()])
        return send

    send = sender(producer)
    pump = EventPump(send)
    pump.start()
    traces = TracePump(sender(trace_producer)) if trace_producer else None
    if traces:
        traces.start()
    worker = TaskWorker(make_shift(pump.publish, traces.publish if traces else None), send, processed, pump)
    ready = asyncio.create_task(_keep_ready(Path(ready_file))) if ready_file else None
    try:
        async for record in consumer:
            msg = Message(record.topic, record.key.decode() if record.key else None, record.value,
                          {k: v.decode() for k, v in (record.headers or [])})
            result = await worker.handle(msg)
            log.info("%s %s → %s", record.topic, record.offset, result)
            await consumer.commit()
            if stop is not None and stop.is_set():
                break
    finally:
        if ready:
            ready.cancel()
            Path(ready_file).unlink(missing_ok=True)
        await pump.stop()
        if traces:
            await traces.stop()
        await consumer.stop()
        await producer.stop()
        if trace_producer:
            await trace_producer.stop()
