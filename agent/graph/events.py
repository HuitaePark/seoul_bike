# Author: Jung Minkyu
# Date: 20OCT2026
# Purpose: Harness의 EventSink 구현. 진행 이벤트(계약 TaskEventMessage)를 ai.events로 내보내고, 그래프가 마무리할 이벤트는 잠시 잡아 둔다.

"""진행 이벤트 → ai.events (docs/AGENT-GRAPH.md 3.3, HARNESS 4절).

이벤트 모양은 Harness가 만든다 (`ProgressEvent.to_json()` = asyncapi `TaskEventMessage`, `seq` 없음).
그래프는 대부분을 그대로 내보내고, Harness 한 구간이 도는 동안 세 가지만 잡아 둔다 (`hold`).
- `FAILED`: 모델 장애면 LLM 없는 경로로 다시 하므로, 실패가 먼저 나가면 안 된다 (`release` 또는 `drop`)
- `APPROVAL_REQUIRED`: 계획 카드에 트럭별 경로(확장 G9)를 붙여 내보낸다 (`release_with`)
- `DONE`: 빠진 문서 근거를 채워 내보낸다 (`release_with`, ISS-23)
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from harness import ProgressEvent

from .contract import plain_hyphens

log = logging.getLogger(__name__)

Publish = Callable[[dict], None]
HELD = {"FAILED", "APPROVAL_REQUIRED", "DONE"}


def message(event: ProgressEvent) -> dict:
    """`ai.events` 메시지. FAILED의 실패 종류는 계약 필드 `code`로 올린다 (openapi v0.4 `TaskEvent.code`).

    Harness는 아직 `harness.code` 확장에 둔다(ISS-05). backend는 계약 밖 필드(`harness`)를 버리므로 올리지 않으면 사라진다.
    """
    body = event.to_json()
    if event.type == "FAILED" and not body.get("code") and event.harness.get("code"):
        body["code"] = event.harness["code"]
    return plain_hyphens(body)


class ShiftEventSink:
    """근무조 그래프가 쓰는 이벤트 출구. `publish`는 Kafka 발행기(운영)나 목록(테스트)."""

    def __init__(self, publish: Publish) -> None:
        self.publish = publish
        self._held: dict[str, list[ProgressEvent]] = {}

    def emit(self, event: ProgressEvent) -> None:
        """Harness가 부른다 (`EventSink` 프로토콜)."""
        if event.type in HELD and event.task_id in self._held:
            self._held[event.task_id].append(event)
            return
        self.publish(message(event))

    def send(self, record: dict, type_: str, **body) -> None:
        """그래프가 직접 내는 이벤트 (LLM 없는 경로, 대체된 작업 닫기)."""
        self.emit(ProgressEvent(type_, record["taskId"], record["traceId"], record["shiftId"], body))

    def hold(self, task_id: str) -> None:
        self._held[task_id] = []

    def release_with(self, task_id: str, card: Callable[[dict], dict], sources: Callable[[list], list]) -> None:
        """잡아 둔 승인 카드·완료를 보강해 내보낸다 (같은 eventId). FAILED는 계속 잡아 둔다."""
        events = self._held.get(task_id, [])
        for event in [e for e in events if e.type in {"APPROVAL_REQUIRED", "DONE"}]:
            if event.type == "APPROVAL_REQUIRED":
                body = {**event.body, "approval": card(event.body["approval"])}
            else:
                body = {**event.body, "sources": sources(event.body.get("sources") or [])}
            self.publish(message(ProgressEvent(event.type, event.task_id, event.trace_id, event.shift_id, body,
                                               event.harness, event.event_id, event.occurred_at)))
            events.remove(event)

    def release(self, task_id: str) -> None:
        """잡아 둔 이벤트를 그대로 내보내고 잡기를 끝낸다."""
        for event in self._held.pop(task_id, []):
            self.publish(message(event))

    def drop(self, task_id: str) -> None:
        """잡아 둔 이벤트를 버린다 (모델 장애 → LLM 없는 경로로 다시 할 때의 FAILED)."""
        dropped = self._held.pop(task_id, [])
        if dropped:
            log.info("작업 %s: 대체 경로로 넘어가며 이벤트 %d건을 내보내지 않음", task_id, len(dropped))
